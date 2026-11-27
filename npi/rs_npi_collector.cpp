#include <algorithm>
#include <cerrno>
#include <cctype>
#include <cstring>
#include <cstdlib>
#include <deque>
#include <fstream>
#include <iostream>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include <sys/stat.h>

#include "npi.h"
#include "npi_hdl.h"
#include "npi_L1.h"
#include "npi_nl.h"

namespace {

enum ExitCode {
  kSuccess = 0,
  kUsageError = 2,
  kPositionsError = 3,
  kElabDbError = 4,
  kTraceRulesError = 5,
  kNpiInitError = 10,
  kNpiLoadError = 11,
  kNpiEndError = 12,
  kOutputError = 13,
  kInternalError = 14
};

const unsigned int kDefaultTraceMaxDepth = 16;
const unsigned int kMaximumTraceMaxDepth = 256;
const unsigned int kMaxTraceObjectVisits = 100000;
const unsigned int kMaxTraceObjectStackDepth = 1024;
const unsigned int kMaxHierarchyDepth = 256;
const unsigned int kMaxHierarchyScopeVisits = 1000000;

struct Options {
  std::string positions_path;
  std::string output_path;
  std::string clk_port;
  std::string rst_port;
  std::string trace_rules_path;
  std::string driver_plan_path;
  std::string trace_max_depth_text;
  std::string elab_db_path;
  unsigned int trace_max_depth;
  bool show_help;
  bool list_hierarchy;
  bool interconnect = false;

  Options()
      : trace_max_depth(kDefaultTraceMaxDepth),
        show_help(false),
        list_hierarchy(false) {}
};

struct HierarchyScopeInfo {
  std::string path;
  std::string name;
  std::string definition;
  std::string kind;
};

struct HierarchyInfo {
  std::vector<HierarchyScopeInfo> scopes;
  std::set<std::string> warnings;
  bool truncated;
  unsigned int visited_scopes;

  HierarchyInfo() : truncated(false), visited_scopes(0) {}
};

struct InterfaceMemberPair {
  std::string source, target;
  std::vector<std::string> path;
};

struct PortInfo {
  std::string connection;
  std::string object_type;
  std::string full_name;
  int direction;
  bool has_direction;
  // Only present in an interconnect inventory. Never use decompiled text as
  // an electrical identity (constants, concatenations and dynamic selects).
  std::string link_kind, identity, interface_type, modport;
  bool member_scan = false;
  std::vector<InterfaceMemberPair> member_pairs;

  PortInfo() : direction(0), has_direction(false) {}
};

struct DriverSource {
  std::string instance;
  std::string module;
  unsigned int depth;
  std::vector<std::string> path;

  DriverSource() : depth(0) {}
};

struct ClockTrace {
  std::string clock_port;
  unsigned int max_depth;
  std::string status;
  std::vector<DriverSource> modules;
  struct Endpoint {
    std::string instance, port, connection;
    unsigned int depth;
    std::vector<std::string> path;
  };
  std::vector<Endpoint> endpoints;
  std::vector<std::string> diagnostics;

  ClockTrace() : max_depth(0), status("complete") {}
};

struct ParameterInfo {
  std::string value;
  bool has_value;

  ParameterInfo() : has_value(false) {}
};

struct InstanceInfo {
  std::string name;
  std::string full_name;
  std::string module;
  std::string file;
  int line;
  bool has_line;
  std::map<std::string, PortInfo> ports;
  std::map<std::string, ParameterInfo> parameters;
  std::vector<DriverSource> clk_sources;
  ClockTrace clock_trace;
  bool has_clock_trace;
  ClockTrace reset_trace;
  bool has_reset_trace;
  std::map<std::string, std::pair<ClockTrace, ClockTrace> > target_traces;

  InstanceInfo() : line(0), has_line(false), has_clock_trace(false), has_reset_trace(false) {}
};

struct PositionInfo {
  bool found;
  std::vector<InstanceInfo> instances;
  std::set<std::string> child_positions;

  PositionInfo() : found(false) {}
};

typedef std::vector<std::pair<std::string, PositionInfo> > PositionResults;
typedef std::map<std::string, std::pair<std::string, std::string> > TraceRules;
typedef std::pair<std::string, std::string> NamePair;
struct DriverPlan {
  std::map<std::string, std::string> kinds;
  std::map<NamePair, std::set<std::string> > inputs;   // (definition, output) -> allowed inputs
  std::map<NamePair, std::set<std::string> > targets;  // (Excel position, RS definition) -> CRGs
};

void print_usage(std::ostream& out, const char* program) {
  out << "Usage: " << program
      << " --positions <newline-file> --output <json-file>"
      << " --clk-port <name> --rst-port <name>"
      << " [--trace-rules <module-tab-clock-tab-reset-file>]"
      << " [--driver-plan <port-mappings-and-targets.tsv>]"
      << " [--trace-max-depth <1..256>]"
      << " [--interconnect]"
      << " --elab-db <Verdi elaborated KDB directory>\n"
      << "       " << program
      << " --list-hierarchy --output <json-file>"
      << " --elab-db <Verdi elaborated KDB directory>\n"
      << "       The elabcom default database name is kdb.elab++; custom "
         "names are accepted.\n";
}

bool take_option_value(int argc, char** argv, int* index,
                       const std::string& option, std::string* value,
                       std::string* error) {
  if (*index + 1 >= argc) {
    *error = "missing value for " + option;
    return false;
  }
  const std::string candidate(argv[*index + 1]);
  if (candidate.empty() || candidate[0] == '-') {
    *error = "missing value for " + option +
             "; the next token looks like another option";
    return false;
  }
  if (!value->empty()) {
    *error = "option specified more than once: " + option;
    return false;
  }
  *value = candidate;
  ++(*index);
  return true;
}

bool parse_options(int argc, char** argv, Options* options, std::string* error) {
  for (int i = 1; i < argc; ++i) {
    const std::string argument(argv[i]);
    if (argument == "--help" || argument == "-h") {
      if (argc != 2) {
        *error = "--help cannot be combined with other arguments";
        return false;
      }
      options->show_help = true;
      return true;
    }
    if (argument == "--positions") {
      if (!take_option_value(argc, argv, &i, argument,
                             &options->positions_path, error)) {
        return false;
      }
    } else if (argument == "--interconnect") {
      if (options->interconnect) { *error = "duplicate --interconnect"; return false; }
      options->interconnect = true;
    } else if (argument == "--list-hierarchy") {
      if (options->list_hierarchy) {
        *error = "option specified more than once: --list-hierarchy";
        return false;
      }
      options->list_hierarchy = true;
    } else if (argument == "--output") {
      if (!take_option_value(argc, argv, &i, argument,
                             &options->output_path, error)) {
        return false;
      }
    } else if (argument == "--clk-port") {
      if (!take_option_value(argc, argv, &i, argument, &options->clk_port,
                             error)) {
        return false;
      }
    } else if (argument == "--rst-port") {
      if (!take_option_value(argc, argv, &i, argument, &options->rst_port,
                             error)) {
        return false;
      }
    } else if (argument == "--trace-rules") {
      if (!take_option_value(argc, argv, &i, argument,
                             &options->trace_rules_path, error)) {
        return false;
      }
    } else if (argument == "--driver-plan") {
      if (!take_option_value(argc, argv, &i, argument, &options->driver_plan_path, error)) return false;
    } else if (argument == "--trace-max-depth") {
      if (!take_option_value(argc, argv, &i, argument,
                             &options->trace_max_depth_text, error)) {
        return false;
      }
    } else if (argument == "--elab-db") {
      if (!take_option_value(argc, argv, &i, argument, &options->elab_db_path,
                             error)) {
        return false;
      }
    } else {
      *error = "unexpected argument '" + argument +
               "'; raw Verdi arguments, RTL sources, and filelists are not "
                "accepted; use --elab-db <Verdi elaborated KDB directory>";
      return false;
    }
  }

  if (options->output_path.empty() || options->elab_db_path.empty()) {
    *error = "--output and --elab-db are required";
    return false;
  }
  if (options->list_hierarchy) {
    if (!options->positions_path.empty() || !options->clk_port.empty() ||
        !options->rst_port.empty() || !options->trace_rules_path.empty() ||
        !options->trace_max_depth_text.empty() || !options->driver_plan_path.empty() || options->interconnect) {
      *error = "--list-hierarchy cannot be combined with --positions, "
               "--clk-port, --rst-port, --trace-rules, --interconnect, or --trace-max-depth";
      return false;
    }
    return true;
  }
  if (options->positions_path.empty() || options->clk_port.empty() ||
      options->rst_port.empty()) {
    *error = "--positions, --output, --clk-port, --rst-port, and --elab-db "
             "are required";
    return false;
  }
  if (!options->trace_max_depth_text.empty()) {
    unsigned int value = 0;
    for (std::string::const_iterator it = options->trace_max_depth_text.begin();
         it != options->trace_max_depth_text.end(); ++it) {
      if (*it < '0' || *it > '9') {
        *error = "--trace-max-depth must be an ASCII integer from 1 to 256";
        return false;
      }
      const unsigned int digit = static_cast<unsigned int>(*it - '0');
      if (value > (kMaximumTraceMaxDepth - digit) / 10) {
        *error = "--trace-max-depth must be an ASCII integer from 1 to 256";
        return false;
      }
      value = value * 10 + digit;
    }
    if (value < 1 || value > kMaximumTraceMaxDepth) {
      *error = "--trace-max-depth must be an ASCII integer from 1 to 256";
      return false;
    }
    options->trace_max_depth = value;
  } else {
    options->trace_max_depth = kDefaultTraceMaxDepth;
  }
  return true;
}

bool validate_elab_db_path(const std::string& input, std::string* normalized,
                           std::string* error) {
  *normalized = input;
  while (normalized->size() > 1 &&
         (*normalized)[normalized->size() - 1] == '/') {
    normalized->erase(normalized->size() - 1);
  }

  struct stat status;
  if (stat(normalized->c_str(), &status) != 0) {
    const int stat_error = errno;
    *error = "Verdi elaborated KDB path is not accessible: " + *normalized +
             " (" + std::strerror(stat_error) + ")";
    return false;
  }
  if (!S_ISDIR(status.st_mode)) {
    *error = "Verdi elaborated KDB path must be a directory: " + *normalized;
    return false;
  }
  return true;
}

std::string trim_ascii(const std::string& input) {
  std::string::size_type begin = 0;
  while (begin < input.size() &&
         std::isspace(static_cast<unsigned char>(input[begin]))) {
    ++begin;
  }
  std::string::size_type end = input.size();
  while (end > begin &&
         std::isspace(static_cast<unsigned char>(input[end - 1]))) {
    --end;
  }
  return input.substr(begin, end - begin);
}

bool read_positions(const std::string& path, std::vector<std::string>* positions,
                    std::string* error) {
  std::ifstream input(path.c_str(), std::ios::in | std::ios::binary);
  if (!input) {
    *error = "cannot open positions file: " + path;
    return false;
  }

  std::set<std::string> seen;
  std::string line;
  while (std::getline(input, line)) {
    const std::string position = trim_ascii(line);
    if (!position.empty() && seen.insert(position).second) {
      positions->push_back(position);
    }
  }
  if (!input.eof()) {
    *error = "failed while reading positions file: " + path;
    return false;
  }
  if (positions->empty()) {
    *error = "positions file contains no non-empty hierarchy paths: " + path;
    return false;
  }
  return true;
}

bool read_driver_plan(const std::string& path, DriverPlan* plan, std::string* error) {
  std::ifstream file(path.c_str(), std::ios::binary);
  if (!file) { *error = "cannot open driver plan: " + path; return false; }
  std::string line;
  unsigned int number = 0;
  while (std::getline(file, line)) {
    ++number;
    if (!line.empty() && line.back() == '\r') line.pop_back();
    if (line.empty()) continue;
    std::istringstream record(line);
    std::vector<std::string> fields;
    std::string field;
    while (std::getline(record, field, '\t')) fields.push_back(field);
    if (line.back() != '\t' && std::find(fields.begin(), fields.end(), "") == fields.end()) {
      if (fields.size() == 5 && fields[0] == "R" &&
          (fields[1] == "DIV" || fields[1] == "GATE" || fields[1] == "OCC") &&
          fields[3] != fields[4] && (!plan->kinds.count(fields[2]) || plan->kinds[fields[2]] == fields[1])) {
        plan->kinds[fields[2]] = fields[1];
        if (plan->inputs[NamePair(fields[2], fields[3])].insert(fields[4]).second) continue;
      } else if (fields.size() == 4 && fields[0] == "T") {
        plan->targets[NamePair(fields[1], fields[2])].insert(fields[3]);
        continue;
      }
    }
    std::ostringstream msg; msg << "invalid driver plan at line " << number;
    *error = msg.str(); return false;
  }
  if (!file.eof()) { *error = "failed reading driver plan: " + path; return false; }
  return true;
}

bool read_trace_rules(const std::string& path, const std::string& default_rst, TraceRules* rules,
                      std::string* error) {
  std::ifstream input(path.c_str(), std::ios::in | std::ios::binary);
  if (!input) {
    *error = "cannot open trace rules file: " + path;
    return false;
  }

  unsigned int line_number = 0;
  std::string line;
  while (std::getline(input, line)) {
    ++line_number;
    if (!line.empty() && line[line.size() - 1] == '\r') {
      line.erase(line.size() - 1);
    }
    if (trim_ascii(line).empty()) {
      continue;
    }
    const std::string::size_type separator = line.find('\t');
    const std::string::size_type second = line.find('\t', separator == std::string::npos ? 0 : separator + 1);
    if (separator == std::string::npos ||
        (second != std::string::npos && line.find('\t', second + 1) != std::string::npos)) {
      std::ostringstream message;
      message << "trace rules line " << line_number
              << " must contain module<TAB>clock_port[<TAB>reset_port]";
      *error = message.str();
      return false;
    }
    const std::string module = trim_ascii(line.substr(0, separator));
    const std::string clock_port = trim_ascii(line.substr(separator + 1, second - separator - 1));
    const std::string reset_port = second == std::string::npos ? default_rst : trim_ascii(line.substr(second + 1));
    const std::pair<std::string, std::string> ports(clock_port, reset_port);
    if (module.empty() || clock_port.empty() || reset_port.empty()) {
      std::ostringstream message;
      message << "trace rules line " << line_number
              << " has an empty module, clock or reset port";
      *error = message.str();
      return false;
    }
    TraceRules::const_iterator existing = rules->find(module);
    if (existing != rules->end() && existing->second != ports) {
      std::ostringstream message;
      message << "trace rules define conflicting clock/reset ports for module "
              << module;
      *error = message.str();
      return false;
    }
    (*rules)[module] = ports;
  }
  if (!input.eof()) {
    *error = "failed while reading trace rules file: " + path;
    return false;
  }
  if (rules->empty()) {
    *error = "trace rules file contains no module<TAB>clock_port entries: " +
             path;
    return false;
  }
  return true;
}

class MutableArgv {
 public:
  explicit MutableArgv(const std::vector<std::string>& arguments) {
    storage_.reserve(arguments.size());
    argv_.reserve(arguments.size() + 1);
    for (std::vector<std::string>::const_iterator it = arguments.begin();
         it != arguments.end(); ++it) {
      storage_.push_back(std::vector<char>(it->begin(), it->end()));
      storage_.back().push_back('\0');
    }
    for (std::vector<std::vector<char> >::iterator it = storage_.begin();
         it != storage_.end(); ++it) {
      argv_.push_back(&(*it)[0]);
    }
    argv_.push_back(NULL);
  }

  int argc() const { return static_cast<int>(storage_.size()); }
  char** argv() { return &argv_[0]; }

 private:
  std::vector<std::vector<char> > storage_;
  std::vector<char*> argv_;
};

class NpiSession {
 public:
  NpiSession() : active_(false) {}

  bool initialize(int& argc, char**& argv) {
    active_ = npi_init(argc, argv) != 0;
    return active_;
  }

  bool finish() {
    if (!active_) {
      return true;
    }
    active_ = false;
    return npi_end() != 0;
  }

  ~NpiSession() {
    if (active_) {
      npi_end();
    }
  }

 private:
  bool active_;
};

std::vector<std::string> queryable_top_instances() {
  std::vector<std::string> names;
  npiHandle iterator = npi_iterate(npiInstance, NULL);
  if (iterator == NULL) {
    return names;
  }

  npiHandle top = NULL;
  while ((top = npi_scan(iterator)) != NULL) {
    const NPI_BYTE8* value = npi_get_str(npiFullName, top);
    if (value != NULL && value[0] != '\0') {
      names.push_back(reinterpret_cast<const char*>(value));
    }
    npi_release_handle(top);
  }
  std::sort(names.begin(), names.end());
  names.erase(std::unique(names.begin(), names.end()), names.end());
  return names;
}

std::string language_string(NPI_INT32 property, npiHandle object) {
  if (object == NULL) {
    return std::string();
  }
  const NPI_BYTE8* value = npi_get_str(property, object);
  return value == NULL ? std::string() : std::string(value);
}

std::string netlist_string(NPI_INT32 property, npiNlHandle object) {
  if (object == NULL) {
    return std::string();
  }
  const NPI_BYTE8* value = npi_nl_get_str(property, object);
  return value == NULL ? std::string() : std::string(value);
}

std::string pointer_key(const void* pointer) {
  std::ostringstream out;
  out << pointer;
  return out.str();
}

std::string language_object_key(npiHandle object) {
  const NPI_INT32 type = npi_get(npiType, object);
  std::string name = language_string(npiFullName, object);
  if (name.empty()) {
    name = language_string(npiName, object);
  }
  if (name.empty()) {
    name = pointer_key(object);
  }
  std::ostringstream out;
  out << type << ':' << name;
  return out.str();
}

std::string netlist_object_key(npiNlHandle object) {
  const NPI_INT32 type = npi_nl_get(npiNlType, object);
  std::string name = netlist_string(npiNlFullName, object);
  if (name.empty()) {
    name = netlist_string(npiNlName, object);
  }
  if (name.empty()) {
    name = pointer_key(object);
  }
  std::ostringstream out;
  out << type << ':' << name;
  return out.str();
}

class HierarchyCollector {
 public:
  HierarchyInfo collect() {
    npiHandle iterator = npi_iterate(npiInstance, NULL);
    if (iterator == NULL) {
      mark_truncated("NPI hierarchy has no queryable top scope");
      return result_;
    }

    npiHandle top = NULL;
    while ((top = npi_scan(iterator)) != NULL) {
      collect_scope(top, 0);
      npi_release_handle(top);
      if (scope_visit_limit_reached_) {
        npi_release_handle(iterator);
        break;
      }
    }

    if (result_.scopes.empty()) {
      mark_truncated("NPI hierarchy has no supported top scope");
    }

    std::sort(result_.scopes.begin(), result_.scopes.end(), scope_less);
    result_.scopes.erase(
        std::unique(result_.scopes.begin(), result_.scopes.end(),
                    same_scope_path),
        result_.scopes.end());
    return result_;
  }

 private:
  static bool supported_scope_type(NPI_INT32 type) {
    return type == npiModule || type == npiInterface || type == npiProgram ||
           type == npiGenScope;
  }

  static std::string scope_kind(NPI_INT32 type) {
    if (type == npiModule) {
      return "module";
    }
    if (type == npiInterface) {
      return "interface";
    }
    if (type == npiProgram) {
      return "program";
    }
    if (type == npiGenScope) {
      return "gen_scope";
    }
    return "unknown";
  }

  static bool scope_less(const HierarchyScopeInfo& left,
                         const HierarchyScopeInfo& right) {
    if (left.path != right.path) {
      return left.path < right.path;
    }
    if (left.kind != right.kind) {
      return left.kind < right.kind;
    }
    if (left.name != right.name) {
      return left.name < right.name;
    }
    return left.definition < right.definition;
  }

  static bool same_scope_path(const HierarchyScopeInfo& left,
                              const HierarchyScopeInfo& right) {
    return left.path == right.path;
  }

  void mark_truncated(const std::string& warning) {
    result_.truncated = true;
    result_.warnings.insert(warning);
  }

  void collect_scope(npiHandle scope, unsigned int depth) {
    if (scope == NULL) {
      return;
    }
    const NPI_INT32 type = npi_get(npiType, scope);
    const bool display_scope = supported_scope_type(type);
    const std::string key = language_object_key(scope);
    if (visited_scope_keys_.find(key) != visited_scope_keys_.end()) {
      return;
    }
    if (result_.visited_scopes >= kMaxHierarchyScopeVisits) {
      scope_visit_limit_reached_ = true;
      mark_truncated(
          "hierarchy traversal exceeded the scope visit limit of 1000000");
      return;
    }
    visited_scope_keys_.insert(key);
    ++result_.visited_scopes;
    if (depth > kMaxHierarchyDepth) {
      mark_truncated("hierarchy traversal exceeded the depth limit of 256");
      return;
    }

    if (display_scope) {
      HierarchyScopeInfo info;
      info.path = language_string(npiFullName, scope);
      info.name = language_string(npiName, scope);
      if (type != npiGenScope) {
        info.definition = language_string(npiDefName, scope);
      }
      info.kind = scope_kind(type);
      if (info.path.empty()) {
        mark_truncated("hierarchy scope has no full name and was omitted");
      } else {
        result_.scopes.push_back(info);
      }
    }

    // npiInternalScope can expose vendor or language-specific container
    // scopes. They are not selectable positions, but their supported
    // descendants must remain discoverable.
    npiHandle iterator = npi_iterate(npiInternalScope, scope);
    if (iterator == NULL) {
      return;
    }
    npiHandle child = NULL;
    while ((child = npi_scan(iterator)) != NULL) {
      collect_scope(child, depth + 1);
      npi_release_handle(child);
      if (scope_visit_limit_reached_) {
        npi_release_handle(iterator);
        break;
      }
    }
  }

  HierarchyInfo result_;
  std::set<std::string> visited_scope_keys_;
  bool scope_visit_limit_reached_ = false;
};

class Collector {
 public:
  Collector(const std::string& fallback_clk_port,
            const std::string& fallback_rst_port,
            const TraceRules& trace_rules, const DriverPlan& plan, unsigned int trace_max_depth, bool interconnect,
            const std::vector<std::string>& positions)
      : fallback_clk_port_(fallback_clk_port),
        fallback_rst_port_(fallback_rst_port),
        trace_rules_(trace_rules), plan_(plan),
        trace_max_depth_(trace_max_depth), interconnect_(interconnect),
        boundaries_(positions.begin(), positions.end()) {}

  PositionInfo collect_position(const std::string& position) {
    current_position_ = position;
    PositionInfo result;
    npiHandle scope = npi_handle_by_name(position.c_str(), NULL);
    if (scope == NULL) {
      return result;
    }

    const NPI_INT32 scope_type = npi_get(npiType, scope);
    if (scope_type != npiModule && scope_type != npiInterface &&
        scope_type != npiProgram && scope_type != npiGenScope) {
      add_warning("hierarchy position does not resolve to a supported scope: " +
                  position + " (" + language_string(npiType, scope) + ")");
      npi_release_handle(scope);
      return result;
    }

    result.found = true;
    std::set<std::string> visited_scopes;
    std::set<std::string> emitted_modules;
    collect_boundary_contents(scope, 0, true, &visited_scopes, &emitted_modules, &result);
    npi_release_handle(scope);

    std::sort(result.instances.begin(), result.instances.end(),
              instance_less);
    return result;
  }

  const std::set<std::string>& warnings() const { return warnings_; }

  void finish_member_links(PositionResults* positions) {
    if (!interconnect_) return;
    std::map<std::string, std::vector<PortInfo*> > ports;
    for (auto& pos : *positions) for (auto& inst : pos.second.instances)
      for (auto& item : inst.ports) {
        auto& p = item.second;
        if (p.link_kind == "interface" && !p.identity.empty()) {
          ports[p.identity].push_back(&p);
        }
      }
    if (ports.size() < 2) {
      for (auto& entry : ports) for (auto p : entry.second) p->member_scan=true;
      return;
    }
    std::map<std::string,std::string> owners;  // actual member object -> actual bundle
    std::map<std::pair<std::string,std::string>,std::set<std::string> > visible;
    std::set<std::string> scopes;
    bool limited=false;
    size_t objects=0;
    for (const auto& group : ports) {
      npiHandle h = npi_handle_by_name(group.first.c_str(), NULL);
      if (!h) {
        add_warning("interface member declarations unavailable at "+group.first);
        continue;
      }
      for (int rel : {npiNet, npiVariables}) {
        npiHandle it = npi_iterate(rel,h), m;
        while (it && (m=npi_scan(it)) != NULL) {
          if (++objects>200000) {
            limited=true; npi_release_handle(m); npi_release_handle(it); break;
          }
          if (whole_signal(m)) {
            const std::string full=language_string(npiFullName,m);
            if (full == group.first + "." + language_string(npiName,m)) owners[full]=group.first;
          }
          npi_release_handle(m);
        }
      }
      if (limited) { npi_release_handle(h); break; }
      std::set<std::string> views;
      for (auto p : group.second) if (!p->modport.empty()) views.insert(p->modport);
      if (!views.empty()) {
        npiHandle mods=npi_iterate(npiModport,h), mod;
        while (mods && (mod=npi_scan(mods))!=NULL) {
          const std::string view=language_string(npiName,mod);
          if (views.count(view)) {
            auto& exposed=visible[{group.first,view}];
            npiHandle it=npi_iterate(npiMpPort,mod), mp;
            while (it && (mp=npi_scan(it))!=NULL) {
              if (++objects>200000) { limited=true; npi_release_handle(mp); npi_release_handle(it); break; }
              const auto name=language_string(npiName,mp);
              npiHandle actual=member_operand(npi_handle(npiExpr,mp),&limited);
              if (whole_signal(actual) && language_string(npiFullName,actual)==group.first+"."+name)
                exposed.insert(group.first+"."+name);
              if (actual) npi_release_handle(actual);
              npi_release_handle(mp);
            }
          }
          npi_release_handle(mod);
          if (limited) { npi_release_handle(mods); break; }
        }
      }
      for (auto p : group.second) p->member_scan=true;
      npiHandle cur=h;
      for (int depth=0; cur && depth<256; ++depth) {
        const std::string name=language_string(npiFullName,cur);
        if (!scopes.insert(name).second) { npi_release_handle(cur); cur=NULL; break; }
        const int type=npi_get(npiType,cur);
        if (type!=npiModule && type!=npiInterface && type!=npiGenScope) {
          npi_release_handle(cur); cur=NULL; break;
        }
        npiHandle parent=npi_handle(npiScope,cur);
        npi_release_handle(cur);
        cur=parent;
      }
      if (cur) { npi_release_handle(cur); add_warning("interface member scan: ancestor depth limit reached"); }
      if (limited) break;
    }
    std::map<std::string,std::set<std::string> > next;
    std::set<std::string> visited;
    for (const auto& name : scopes) {
      if (limited) break;
      npiHandle h=npi_handle_by_name(name.c_str(),NULL);
      if (!h) continue;
      scan_member_scope(h,0,&visited,&next,&objects,&limited);
      npi_release_handle(h);
    }
    // Walk directed assign arcs only. Sinks sharing a driver are never
    // equated, and a third interface member ends a path (no invented bypass).
    size_t visits=0, proof_hops=0;
    bool trace_limit=false;
    for (const auto& src : owners) {
      if (!next.count(src.first)) continue;
      std::deque<std::string> queue(1,src.first);
      std::map<std::string,std::string> parent{{src.first,""}};
      while (!queue.empty() && !trace_limit) {
        const std::string at=queue.front(); queue.pop_front();
        const auto edges=next.find(at);
        if (edges==next.end()) continue;
        for (const auto& dst : edges->second) {
          if (++visits>200000) { trace_limit=true; break; }
          if (!parent.emplace(dst,at).second) continue;
          const auto member=owners.find(dst);
          if (member==owners.end()) { queue.push_back(dst); continue; }
          if (src.second==member->second) continue;
          InterfaceMemberPair pair;
          pair.source=src.first; pair.target=dst;
          for (std::string hop=dst; !hop.empty(); hop=parent.at(hop)) {
            pair.path.push_back(hop);
            if (pair.path.size()>128) break;
          }
          if (pair.path.back()!=src.first || pair.path.size()>128) { limited=true; continue; }
          size_t copies=ports[src.second].size()+ports[member->second].size();
          if (copies>(1000000-proof_hops)/pair.path.size()) { trace_limit=true; break; }
          proof_hops+=copies*pair.path.size();
          std::reverse(pair.path.begin(),pair.path.end());
          for (const auto& end : {src.first,dst}) {
            const auto& bundle=owners.at(end);
            for (auto p : ports[bundle])
              if (p->modport.empty() || visible[{bundle,p->modport}].count(end)) p->member_pairs.push_back(pair);
          }
        }
      }
      if (trace_limit) break;
    }
    if (limited || trace_limit) add_warning("interface member connectivity scan limited; missing links are inconclusive");
  }

 private:
  // On the tested Verdi ABI an XMR in a continuous assignment is an ExtRef,
  // not a RefObj. Its npiActual is the elaborated declaration (or select).
  // Do not guess from npiName/decompiled RTL when actual resolution fails.
  npiHandle member_operand(npiHandle obj, bool* limited) {
    for (unsigned hop=0; obj && hop<32; ++hop) {
      const int type=npi_get(npiType,obj);
      if (type!=npiRefObj && type!=npiExtRef) break;
      npiHandle actual=npi_handle(npiActual,obj);
      npi_release_handle(obj);
      obj=actual;
    }
    if (obj && (npi_get(npiType,obj)==npiRefObj || npi_get(npiType,obj)==npiExtRef)) *limited=true;
    return obj;
  }

  void scan_member_scope(npiHandle scope, unsigned depth, std::set<std::string>* visited,
      std::map<std::string,std::set<std::string> >* next, size_t* objects, bool* limited) {
    if (!visited->insert(language_string(npiFullName,scope)).second) return;
    if (depth>64) { *limited=true; return; }
    npiHandle assigns=npi_iterate(npiContAssign,scope), a;
    while (assigns && (a=npi_scan(assigns))!=NULL) {
      if (++*objects>200000) { *limited=true; npi_release_handle(a); npi_release_handle(assigns); return; }
      npiHandle lhs=member_operand(npi_handle(npiLhs,a),limited);
      npiHandle rhs=member_operand(npi_handle(npiRhs,a),limited);
      if (whole_signal(lhs) && whole_signal(rhs) && npi_get(npiSize,lhs)>0 &&
          npi_get(npiSize,lhs)==npi_get(npiSize,rhs)) {
        const std::string l=language_string(npiFullName,lhs), r=language_string(npiFullName,rhs);
        if (l.find('.')!=std::string::npos && r.find('.')!=std::string::npos && l!=r) (*next)[r].insert(l);
      }
      if (lhs) npi_release_handle(lhs);
      if (rhs) npi_release_handle(rhs);
      npi_release_handle(a);
    }
    npiHandle children=npi_iterate(npiInternalScope,scope), child;
    while (children && (child=npi_scan(children))!=NULL) {
      if (++*objects>200000) *limited=true;
      if (!*limited && npi_get(npiType,child)==npiGenScope)
        scan_member_scope(child,depth+1,visited,next,objects,limited);
      npi_release_handle(child);
      if (*limited) { npi_release_handle(children); return; }
    }
  }
  static const unsigned int kMaxScopeDepth = 256;

  struct ModuleHit {
    std::string instance;
    std::string module;
    std::string port;
    bool is_output = false;
  };

  struct ModuleFrontier {
    std::string instance;
    std::string module;
    std::string output;
    unsigned int depth;
    std::vector<std::string> path;

    ModuleFrontier() : depth(0) {}
  };

  struct TraceState {
    TraceState(const std::string& clock_port, unsigned int max_depth)
        : object_visits(0), depth_limited(false), unresolved(false) {
      trace.clock_port = clock_port;
      trace.max_depth = max_depth;
    }

    ClockTrace trace;
    unsigned int object_visits;
    bool depth_limited;
    bool unresolved;
    std::map<std::pair<std::string, std::string>, ClockTrace::Endpoint> endpoints;
    std::map<std::string, unsigned int> best_object_depth;
    std::set<NamePair> expanded_outputs;
    std::map<std::string, DriverSource> modules_by_instance;
    std::set<std::string> diagnostic_keys;
  };

  static bool instance_less(const InstanceInfo& left,
                            const InstanceInfo& right) {
    if (left.full_name != right.full_name) {
      return left.full_name < right.full_name;
    }
    return left.name < right.name;
  }

  static bool module_hit_less(const ModuleHit& left, const ModuleHit& right) {
    if (left.instance != right.instance) {
      return left.instance < right.instance;
    }
    return std::make_pair(left.module, left.port) < std::make_pair(right.module, right.port);
  }

  static bool source_less(const DriverSource& left,
                          const DriverSource& right) {
    if (left.depth != right.depth) {
      return left.depth < right.depth;
    }
    if (left.instance != right.instance) {
      return left.instance < right.instance;
    }
    if (left.module != right.module) {
      return left.module < right.module;
    }
    return left.path < right.path;
  }

  void add_warning(const std::string& warning) { warnings_.insert(warning); }

  void add_trace_diagnostic(TraceState* state, const std::string& diagnostic) {
    if (state->diagnostic_keys.insert(diagnostic).second) {
      state->trace.diagnostics.push_back(diagnostic);
    }
  }

  void mark_trace_unresolved(TraceState* state,
                             const std::string& diagnostic) {
    state->unresolved = true;
    add_trace_diagnostic(state, diagnostic);
  }

  void collect_boundary_contents(
      npiHandle scope, unsigned int depth, bool direct, std::set<std::string>* visited_scopes,
      std::set<std::string>* emitted_modules, PositionInfo* result) {
    if (scope == NULL) {
      return;
    }
    if (depth > kMaxScopeDepth) {
      add_warning("language hierarchy traversal exceeded the depth limit at " +
                  language_string(npiFullName, scope));
      return;
    }
    if (!visited_scopes->insert(language_object_key(scope)).second) {
      return;
    }

    npiHandle iterator = npi_iterate(npiInternalScope, scope);
    if (iterator == NULL) {
      return;
    }

    npiHandle child = NULL;
    while ((child = npi_scan(iterator)) != NULL) {
      if (++scope_visits_ > kMaxHierarchyScopeVisits) {
        add_warning("position subtree traversal exceeded the scope budget; inventory is incomplete");
        npi_release_handle(child);
        npi_release_handle(iterator);
        break;
      }
      const NPI_INT32 child_type = npi_get(npiType, child);
      const std::string path = language_string(npiFullName, child);
      if (child_type == npiModule) {
        const std::string key = language_object_key(child);
        if (emitted_modules->insert(key).second) {
          // Keep name/definition for exact wrong-module diagnostics. Do not
          // collect every deep non-RS wrapper's parameters, ports and traces.
          result->instances.push_back(build_instance(child, !direct));
        }
      }
      if (boundaries_.count(path) && path != current_position_) {
        // Its contents are collected exactly once in the child's own bucket.
        // The boundary module itself, like any other child, is in our bucket.
        result->child_positions.insert(path);
        boundary_parent_[path] = current_position_;
      } else {
        // Like the DUT picker, cross vendor-specific container scopes too.
        // Only module instances become candidates; other scopes are traversed.
        collect_boundary_contents(child, depth + 1, direct && child_type == npiGenScope,
                                  visited_scopes, emitted_modules, result);
      }
      npi_release_handle(child);
    }
  }

  npiHandle resolve_ref_object(npiHandle object, TraceState* trace_state = NULL) {
    for (unsigned int depth = 0; object != NULL && depth < 32; ++depth) {
      if (npi_get(npiType, object) != npiRefObj) {
        return object;
      }
      npiHandle actual = npi_handle(npiActual, object);
      if (actual == NULL) {
        return object;
      }
      npi_release_handle(object);
      object = actual;
    }
    if (object != NULL && npi_get(npiType, object) == npiRefObj) {
      const std::string message =
          "npiRefObj resolution exceeded the depth limit at " +
          language_string(npiFullName, object);
      if (trace_state == NULL) {
        add_warning(message);
      } else {
        mark_trace_unresolved(trace_state, message);
      }
    }
    return object;
  }

  static bool whole_signal(npiHandle obj) {
    if (!obj) return false;
    const std::string t = language_string(npiType, obj);
    return t == "npiNet" || t == "npiReg" || t == "npiIntegerVar" || t == "npiLogicVar" ||
           t == "npiBitVar" || t == "npiIntVar" || t == "npiByteVar" ||
           t == "npiLongIntVar" || t == "npiShortIntVar";
  }

  // Index direct formal-to-actual bindings only in enclosing modules visited.
  // Comparing low and high objects (including width) avoids treating a
  // constant, concatenation, truncated bus or logic transform as a wire.
  const std::map<std::string, std::string>& parent_wires(npiHandle scope) {
    const std::string path = language_string(npiFullName, scope);
    auto entry = parent_wires_.emplace(path, std::map<std::string, std::string>());
    auto& wires = entry.first->second;
    if (!entry.second) return wires;
    npiHandle iter = npi_iterate(npiPort, scope);
    npiHandle port;
    while (iter && (port = npi_scan(iter)) != NULL) {
      const int kind = npi_get(npiPortType, port);
      if (kind != npiModportPort && kind != npiInterfacePort) {
        npiHandle low = resolve_ref_object(npi_handle(npiLowConn, port));
        npiHandle high = resolve_ref_object(npi_handle(npiHighConn, port));
        if (whole_signal(low) && whole_signal(high) && npi_get(npiSize, low) > 0 &&
            npi_get(npiSize, low) == npi_get(npiSize, high)) {
          std::string l = language_string(npiFullName, low), h = language_string(npiFullName, high);
          if (l.find('.') != std::string::npos && h.find('.') != std::string::npos && l != h) {
            auto added = wires.emplace(l, h);
            if (!added.second && added.first->second != h) added.first->second.clear();
          }
        }
        if (low) npi_release_handle(low);
        if (high) npi_release_handle(high);
      }
      npi_release_handle(port);
    }
    return wires;
  }

  std::string signal_identity(npiHandle actual, const std::string& name) {
    auto cached = equivalent_nets_.find(name);
    if (cached != equivalent_nets_.end()) return cached->second;
    std::string upper = name;
    std::set<std::string> seen;
    npiHandle current = actual;
    for (int hop = 0; current && hop < 64 && seen.insert(upper).second; ++hop) {
      npiHandle scope = npi_handle(npiScope, current);
      if (!scope) break;
      if (npi_get(npiType, scope) != npiModule && npi_get(npiType, scope) != npiInterface) {
        npi_release_handle(scope);
        break;
      }
      const auto& wires = parent_wires(scope);
      npi_release_handle(scope);
      const auto it = wires.find(upper);
      if (it == wires.end() || it->second.empty()) break;
      upper = it->second;
      if (current != actual) npi_release_handle(current);
      current = npi_handle_by_name(upper.c_str(), NULL);
    }
    if (current && current != actual) npi_release_handle(current);
    cached = equivalent_nets_.find(upper);
    if (cached != equivalent_nets_.end()) return equivalent_nets_[name] = cached->second;
    std::string best = upper;
    if (npi_get(npiSize, actual) == 1) {
      nlHdlVec_t equivalents;
      std::vector<char> ref(upper.begin(), upper.end());
      ref.push_back('\0');
      npi_nl_get_equivalent_signal(&ref[0], equivalents, 0);
      for (auto h : equivalents) {
        if (!h) continue;
        if (npi_nl_get(npiNlType, h) == npiNlDeclNet) {
          const std::string other = netlist_string(npiNlFullName, h);
          // Do not lose bit identity on an equivalent section of a larger net.
          npiHandle obj = other.find('.') == std::string::npos ? NULL : npi_handle_by_name(other.c_str(), NULL);
          if (whole_signal(obj) && npi_get(npiSize, obj) == 1) {
            // Prefer a shallow network, not an arbitrary internal port alias.
            auto rank = [](const std::string& s) {
              return std::make_pair(std::count(s.begin(), s.end(), '.'), s);
            };
            if (rank(other) < rank(best)) best = other;
          }
          if (obj) npi_release_handle(obj);
        }
        npi_nl_release_handle(h);
      }
    }
    equivalent_nets_[upper] = equivalent_nets_[name] = best;
    return best;
  }

  void identify_link(npiHandle port, npiHandle actual, PortInfo* info) {
    const int formal = npi_get(npiPortType, port);
    const int kind = actual ? npi_get(npiType, actual) : 0;
    if (formal == npiInterfacePort || formal == npiModportPort ||
        kind == npiInterface || kind == npiModport) {
      info->link_kind = "interface";
      npiHandle scope = kind == npiModport ? npi_handle(npiScope, actual) : NULL;
      npiHandle intf = kind == npiInterface ? actual : scope;
      if (intf && npi_get(npiType, intf) == npiInterface) {
        info->identity = language_string(npiFullName, intf);
        info->interface_type = language_string(npiDefName, intf);
      }
      if (scope) npi_release_handle(scope);
      if (kind == npiModport) info->modport = language_string(npiName, actual);
      // .bus(link) may have a bare interface on the high side but a
      // bus_if.mst/slv view on the formal side.
      if (formal == npiModportPort) {
        npiHandle low = npi_handle(npiLowConn, port);
        if (low) {
          const std::string decl = language_string(npiDefName, low);
          low = resolve_ref_object(low);
          if (npi_get(npiType, low) == npiModport)
            info->modport = language_string(npiName, low);
          else if (info->modport.empty() && decl != info->interface_type)
            info->modport = decl;
          npi_release_handle(low);
        }
      }
    } else {
      info->link_kind = "signal";
      if (!actual) return;
      const bool whole = whole_signal(actual);
      const bool selection = kind == npiNetBit || kind == npiRegBit || kind == npiBitSelect ||
                             kind == npiPartSelect || kind == npiIndexedPartSelect;
      const std::string path = language_string(npiFullName, actual);
      if (path.find('.') != std::string::npos &&
          (whole || (selection && npi_get(npiConstantSelect, actual) == 1 &&
                     path.find('[') != std::string::npos)))
        info->identity = whole ? signal_identity(actual, path) : path;
    }
  }

  PortInfo read_high_connection(npiHandle port, TraceState* trace_state = NULL,
                                bool links = false) {
    PortInfo result;
    result.full_name = language_string(npiFullName, port);
    const NPI_INT32 direction = npi_get(npiDirection, port);
    if (direction == npiInput || direction == npiOutput ||
        direction == npiInout) {
      result.direction = static_cast<int>(direction);
      result.has_direction = true;
    }
    npiHandle connection = npi_handle(npiHighConn, port);
    if (connection == NULL) {
      if (links) identify_link(port, NULL, &result);
      return result;
    }

    connection = resolve_ref_object(connection, trace_state);
    result.object_type = language_string(npiType, connection);
    if (links) identify_link(port, connection, &result);
    result.connection = language_string(npiFullName, connection);
    if (result.connection.empty()) {
      result.connection = language_string(npiDecompile, connection);
    }
    if (result.connection.empty()) {
      result.connection = language_string(npiName, connection);
    }
    npi_release_handle(connection);
    return result;
  }

  void merge_port(npiHandle port, const std::string& instance_full_name,
                  std::map<std::string, PortInfo>* ports, bool links) {
    if (port == NULL) {
      return;
    }
    const std::string name = language_string(npiName, port);
    if (name.empty()) {
      add_warning("NPI port is missing its name in module instance: " +
                  instance_full_name);
      return;
    }

    const PortInfo info = read_high_connection(port, NULL, links);
    std::map<std::string, PortInfo>::iterator existing = ports->find(name);
    if (existing == ports->end()) {
      ports->insert(std::make_pair(name, info));
      return;
    }
    if (existing->second.connection.empty() && !info.connection.empty()) {
      existing->second.connection = info.connection;
    }
    if (existing->second.object_type.empty() && !info.object_type.empty()) {
      existing->second.object_type = info.object_type;
    }
    if (existing->second.identity.empty() && !info.identity.empty()) {
      existing->second.identity = info.identity;
      existing->second.link_kind = info.link_kind;
    }
    if (existing->second.interface_type.empty()) existing->second.interface_type = info.interface_type;
    if (existing->second.modport.empty()) existing->second.modport = info.modport;
    if (existing->second.full_name.empty() && !info.full_name.empty()) {
      existing->second.full_name = info.full_name;
    }
    if (!existing->second.has_direction && info.has_direction) {
      existing->second.direction = info.direction;
      existing->second.has_direction = true;
    }
  }

  std::map<std::string, PortInfo> collect_ports(
      npiHandle module, const std::string& instance_full_name, bool links = false) {
    std::map<std::string, PortInfo> result;

    npiHandle iterator = module == NULL ? NULL : npi_iterate(npiPort, module);
    if (iterator != NULL) {
      npiHandle port = NULL;
      while ((port = npi_scan(iterator)) != NULL) {
        merge_port(port, instance_full_name, &result, links);
        npi_release_handle(port);
      }
    }

    if (instance_full_name.empty()) {
      return result;
    }
    std::vector<char> mutable_full_name(instance_full_name.begin(),
                                        instance_full_name.end());
    mutable_full_name.push_back('\0');
    hdlVec_t fallback_ports;
    npi_mod_inst_get_port(&mutable_full_name[0], fallback_ports);
    for (hdlVec_t::const_iterator port = fallback_ports.begin();
         port != fallback_ports.end(); ++port) {
      if (*port == NULL) {
        continue;
      }
      merge_port(*port, instance_full_name, &result, links);
      npi_release_handle(*port);
    }
    return result;
  }

  std::map<std::string, ParameterInfo> collect_parameters(npiHandle module) {
    std::map<std::string, ParameterInfo> result;

    npiHandle iterator = npi_iterate(npiParameter, module);
    if (iterator == NULL) {
      return result;
    }

    npiHandle parameter = NULL;
    while ((parameter = npi_scan(iterator)) != NULL) {
      const std::string name = language_string(npiName, parameter);
      ParameterInfo info;
      npiValue value;
      std::memset(&value, 0, sizeof(value));
      value.format = npiBinStrVal;
      if (npi_get_value(parameter, value, false) != 0 &&
          value.value.str != NULL) {
        // NPI reuses its value buffer on the next API call.
        info.value = value.value.str;
        info.has_value = true;
      }

      if (name.empty()) {
        add_warning("NPI parameter is missing its name in module instance: " +
                    language_string(npiFullName, module));
      } else if (!result.insert(std::make_pair(name, info)).second) {
        add_warning("NPI returned duplicate parameter name " + name +
                    " in module instance: " +
                    language_string(npiFullName, module));
      }
      npi_release_handle(parameter);
    }
    return result;
  }

  npiNlHandle owning_instance(npiNlHandle inst_port) {
    npiNlHandle instance = npi_nl_handle(npiNlInst, inst_port);
    if (instance != NULL) {
      return instance;
    }
    if (npi_nl_get(npiNlType, inst_port) != npiNlPseudoInstPort) {
      return NULL;
    }

    npiNlHandle actual = npi_nl_handle(npiNlParent, inst_port);
    if (actual == NULL) {
      return NULL;
    }
    instance = npi_nl_handle(npiNlInst, actual);
    npi_nl_release_handle(actual);
    return instance;
  }

  bool begin_trace_object(npiNlHandle object, unsigned int module_depth,
                          TraceState* state) {
    const std::string key = netlist_object_key(object);
    std::map<std::string, unsigned int>::iterator existing =
        state->best_object_depth.find(key);
    if (existing != state->best_object_depth.end() &&
        existing->second <= module_depth) {
      return false;
    }
    if (state->object_visits >= kMaxTraceObjectVisits) {
      std::ostringstream message;
      message << "signal trace exceeded the " << kMaxTraceObjectVisits
              << " Netlist object budget";
      mark_trace_unresolved(state, message.str());
      return false;
    }
    if (existing == state->best_object_depth.end()) {
      state->best_object_depth.insert(std::make_pair(key, module_depth));
    } else {
      existing->second = module_depth;
    }
    ++state->object_visits;
    return true;
  }

  void add_module_hit(npiNlHandle instance, npiNlHandle port, std::vector<ModuleHit>* hits,
                      TraceState* state) {
    ModuleHit hit;
    hit.instance = netlist_string(npiNlFullName, instance);
    if (hit.instance.empty()) {
      hit.instance = netlist_string(npiNlName, instance);
    }
    hit.module = netlist_string(npiNlDefName, instance);
    if (port != NULL) {
      hit.port = netlist_string(npiNlName, port);
      hit.is_output = npi_nl_get(npiNlDirection, port) == npiNlOutput;
    }
    if (hit.instance.empty() || hit.module.empty()) {
      mark_trace_unresolved(
          state,
          "NPI Netlist module driver is missing its instance or definition name");
      return;
    }
    hits->push_back(hit);
  }

  void walk_netlist_drivers(npiNlHandle object, unsigned int module_depth,
                            unsigned int stack_depth,
                            std::vector<ModuleHit>* hits, TraceState* state) {
    if (object == NULL ||
        (state->unresolved &&
         state->object_visits >= kMaxTraceObjectVisits)) {
      return;
    }
    if (stack_depth > kMaxTraceObjectStackDepth) {
      std::ostringstream message;
      message << "signal trace exceeded the " << kMaxTraceObjectStackDepth
              << " Netlist traversal stack limit";
      mark_trace_unresolved(state, message.str());
      return;
    }
    if (!begin_trace_object(object, module_depth, state)) {
      return;
    }

    const NPI_INT32 type = npi_nl_get(npiNlType, object);
    if (type == npiNlInstPort || type == npiNlPseudoInstPort) {
      const NPI_INT32 direction = npi_nl_get(npiNlDirection, object);
      npiNlHandle instance = owning_instance(object);
      const bool module_cell =
          instance != NULL &&
          npi_nl_get(npiNlCellType, instance) == npiNlModuleCell;
      if (module_cell && direction == npiNlOutput) {
        add_module_hit(instance, object, hits, state);
        npi_nl_release_handle(instance);
        return;
      }
      if (module_cell && direction != npiNlInput) {
        std::string port_name = netlist_string(npiNlFullName, object);
        if (port_name.empty()) {
          port_name = netlist_string(npiNlName, object);
        }
        mark_trace_unresolved(
            state, "NPI Netlist module port has unknown direction: " +
                       port_name);
        npi_nl_release_handle(instance);
        return;  // no proven output; never jump into an unclassified module
      }
      if (instance != NULL && !module_cell && direction != npiNlInput) {
        walk_netlist_drivers(instance, module_depth, stack_depth + 1, hits,
                             state);
        npi_nl_release_handle(instance);
        return;
      }
      if (instance != NULL) {
        npi_nl_release_handle(instance);
      }
    } else if (type == npiNlInst) {
      if (npi_nl_get(npiNlCellType, object) == npiNlModuleCell) {
        mark_trace_unresolved(state, "module driver has no proven output port: " +
                                     netlist_string(npiNlFullName, object));
        return;
      }
    }

    npiNlHandle iterator = npi_nl_iterate(npiNlDriver, object);
    npiNlHandle driver = NULL;
    if (iterator != NULL) {
      while ((driver = npi_nl_scan(iterator)) != NULL) {
        walk_netlist_drivers(driver, module_depth, stack_depth + 1, hits,
                             state);
        npi_nl_release_handle(driver);
      }
    }

    const bool net_object =
        type == npiNlDeclNet || type == npiNlConcatNet ||
        type == npiNlSliceNet || type == npiNlPseudoNet;
    if (!net_object) {
      return;
    }

    // O-2018.09 represents equivalent slice/concat nets through connectivity.
    // Follow only connected net objects so loads cannot become false drivers.
    iterator = npi_nl_iterate(npiNlConnectivity, object);
    if (iterator == NULL) {
      return;
    }
    npiNlHandle connected = NULL;
    while ((connected = npi_nl_scan(iterator)) != NULL) {
      const NPI_INT32 connected_type = npi_nl_get(npiNlType, connected);
      if (connected_type == npiNlDeclNet ||
          connected_type == npiNlConcatNet ||
          connected_type == npiNlSliceNet ||
          connected_type == npiNlPseudoNet) {
        walk_netlist_drivers(connected, module_depth, stack_depth + 1, hits,
                             state);
      }
      npi_nl_release_handle(connected);
    }
  }

  void discover_upstream_modules(npiNlHandle start, unsigned int module_depth,
                                 std::vector<ModuleHit>* hits,
                                 TraceState* state) {
    walk_netlist_drivers(start, module_depth, 0, hits, state);
    std::sort(hits->begin(), hits->end(), module_hit_less);
    std::vector<ModuleHit> unique;
    for (std::vector<ModuleHit>::const_iterator hit = hits->begin();
         hit != hits->end(); ++hit) {
      if (unique.empty() || unique.back().instance != hit->instance ||
          unique.back().module != hit->module || unique.back().port != hit->port) {
        unique.push_back(*hit);
      }
    }
    hits->swap(unique);
  }

  npiNlHandle resolve_trace_start(const std::string& instance_full_name,
                                  const std::string& clock_port,
                                  const PortInfo& port) {
    std::vector<std::string> port_names;
    if (!port.full_name.empty()) {
      port_names.push_back(port.full_name);
    }
    if (!instance_full_name.empty() && !clock_port.empty()) {
      const std::string constructed = instance_full_name + "." + clock_port;
      if (port_names.empty() || port_names.front() != constructed) {
        port_names.push_back(constructed);
      }
    }
    for (std::vector<std::string>::const_iterator name = port_names.begin();
         name != port_names.end(); ++name) {
      npiNlHandle start =
          npi_nl_handle_by_name(name->c_str(), npiNlInstPort);
      if (start == NULL) {
        start = npi_nl_handle_by_name(name->c_str(), npiNlUndefined);
      }
      if (start != NULL) {
        return start;
      }
    }

    if (!port.connection.empty()) {
      npiNlHandle start =
          npi_nl_handle_by_name(port.connection.c_str(), npiNlNet);
      if (start == NULL) {
        start = npi_nl_handle_by_name(port.connection.c_str(), npiNlUndefined);
      }
      if (start != NULL) {
        return start;
      }
    }
    return NULL;
  }

  bool record_module(const ModuleHit& hit, unsigned int depth,
                     const std::vector<std::string>& parent_path,
                     TraceState* state, ModuleFrontier* frontier) {
    DriverSource source;
    source.instance = hit.instance;
    source.module = hit.module;
    source.depth = depth;
    source.path = parent_path;
    source.path.push_back(hit.instance);

    // Capture the specific driven output before deduplicating module expansion.
    // Never substitute a different output of this module or the RS-side net.
    const std::pair<std::string, std::string> key(hit.instance, hit.port);
    std::map<std::pair<std::string, std::string>, ClockTrace::Endpoint>::iterator old = state->endpoints.find(key);
    if (old == state->endpoints.end() || depth < old->second.depth ||
        (depth == old->second.depth && source.path < old->second.path)) {
      ClockTrace::Endpoint endpoint;
      endpoint.instance = hit.instance;
      endpoint.port = hit.port;
      endpoint.depth = depth;
      endpoint.path = source.path;
      if (!hit.port.empty()) {
        if (!source_ports_.count(hit.instance)) {
          npiHandle module = npi_handle_by_name(hit.instance.c_str(), NULL);
          source_ports_[hit.instance] = collect_ports(module, hit.instance);
          if (module != NULL) npi_release_handle(module);
        }
        const std::map<std::string, PortInfo>& ports = source_ports_[hit.instance];
        std::map<std::string, PortInfo>::const_iterator p = ports.find(hit.port);
        if (p != ports.end() && p->second.object_type.find("Operation") == std::string::npos)
          endpoint.connection = p->second.connection;
      }
      state->endpoints[key] = endpoint;
    }
    std::map<std::string, DriverSource>::iterator existing =
        state->modules_by_instance.find(source.instance);
    if (existing == state->modules_by_instance.end() || source.depth < existing->second.depth ||
        (source.depth == existing->second.depth && source.path < existing->second.path))
      state->modules_by_instance[source.instance] = source;
    frontier->instance = source.instance;
    frontier->module = source.module;
    frontier->output = hit.port;
    frontier->depth = source.depth;
    frontier->path = source.path;
    return true;
  }

  // A driver step ends at an observed module output. Only a configured
  // (definition, output) edge can start another step; no fan-in expansion.
  void enqueue_hits(const std::vector<ModuleHit>& hits, unsigned int depth,
                    const std::vector<std::string>& path, std::deque<ModuleFrontier>* queue,
                    TraceState* state) {
    for (const auto& hit : hits) {
      ModuleFrontier next;
      if (record_module(hit, depth, path, state, &next) && hit.is_output &&
          state->expanded_outputs.insert(NamePair(hit.instance, hit.port)).second)
        queue->push_back(next);
    }
  }

  void follow_mapped_inputs(const ModuleFrontier& curr, const std::string& target,
                            std::deque<ModuleFrontier>* queue, TraceState* state) {
    const std::string from = curr.instance + "." + curr.output;
    if (curr.instance == target) {
      add_trace_diagnostic(state, "target reached at " + from);
      return;
    }
    auto rule = plan_.inputs.find(NamePair(curr.module, curr.output));
    if (rule == plan_.inputs.end()) {
      add_trace_diagnostic(state, "stop: no output mapping for " + curr.module + "." + curr.output + " at " + from);
      return;
    }
    if (curr.depth >= state->trace.max_depth) {
      state->depth_limited = true;
      add_trace_diagnostic(state, "stop: driver depth limit at " + from);
      return;
    }
    const auto& ports = source_ports_[curr.instance];  // Language/L1 fallback for partial KDBs
    for (const std::string& input : rule->second) {
      const std::string name = curr.instance + "." + input;
      auto formal = ports.find(input);
      npiNlHandle start = npi_nl_handle_by_name(name.c_str(), npiNlInstPort);
      bool known_input = start && npi_nl_get(npiNlDirection, start) == npiNlInput;
      if (formal != ports.end() && formal->second.has_direction)
        known_input = formal->second.direction == npiInput;
      if (!known_input) {
        if (start) npi_nl_release_handle(start);
        mark_trace_unresolved(state, "stop: mapped input missing or not an input: " + name + " (from " + from + ")");
        continue;
      }
      if (!start && formal != ports.end())
        start = resolve_trace_start(curr.instance, input, formal->second);
      if (!start) {
        mark_trace_unresolved(state, "stop: cannot resolve mapped input " + name);
        continue;
      }
      add_trace_diagnostic(state, plan_.kinds.at(curr.module) + " " + from + " -> " + name);
      std::vector<ModuleHit> hits;
      state->best_object_depth.clear();  // independent driver segment; total work budget is shared
      discover_upstream_modules(start, curr.depth, &hits, state);
      npi_nl_release_handle(start);
      if (hits.empty()) add_trace_diagnostic(state, "stop: no module driver for mapped input " + name);
      enqueue_hits(hits, curr.depth + 1, curr.path, queue, state);
    }
  }

  ClockTrace trace_clock(const std::string& instance_full_name,
                         const std::string& clock_port,
                         const PortInfo& port, const std::string& target, bool reset = false) {
    // The same leaf net name can occur in different hierarchy scopes when a
    // partial KDB cannot provide a canonical connection full name.
    std::string cache_key =
        instance_full_name + "\x1f" + clock_port + "\x1f" + target + "\x1f" + (reset ? "R" : "C");
    if (!port.connection.empty() &&
        port.object_type.find("Operation") == std::string::npos) {
      cache_key += port.connection;
    } else if (!port.full_name.empty()) {
      cache_key += port.full_name;
    } else {
      cache_key += instance_full_name + "." + clock_port;
    }
    std::map<std::string, ClockTrace>::const_iterator cached =
        clock_trace_cache_.find(cache_key);
    if (cached != clock_trace_cache_.end()) {
      return cached->second;
    }

    TraceState state(clock_port, trace_max_depth_);
    npiNlHandle start =
        resolve_trace_start(instance_full_name, clock_port, port);
    if (start == NULL) {
      mark_trace_unresolved(
          &state, "NPI Netlist could not resolve trace start port: " +
                      instance_full_name + "." + clock_port);
    } else {
      std::vector<ModuleHit> hits;
      discover_upstream_modules(start, 0, &hits, &state);
      npi_nl_release_handle(start);

      std::deque<ModuleFrontier> queue;
      const std::vector<std::string> empty_path;
      enqueue_hits(hits, 1, empty_path, &queue, &state);
      while (!queue.empty()) {
        const ModuleFrontier current = queue.front();
        queue.pop_front();
        follow_mapped_inputs(current, target, &queue, &state);
      }
      if (state.modules_by_instance.empty() && !state.unresolved) {
        add_trace_diagnostic(
            &state, "no upstream module output was resolved for start port: " +
                        instance_full_name + "." + clock_port);
      }
    }

    for (std::map<std::string, DriverSource>::const_iterator source =
             state.modules_by_instance.begin();
         source != state.modules_by_instance.end(); ++source) {
      state.trace.modules.push_back(source->second);
    }
    for (const auto& item : state.endpoints) state.trace.endpoints.push_back(item.second);
    std::sort(state.trace.modules.begin(), state.trace.modules.end(),
              source_less);
    if (state.unresolved) {
      state.trace.status = "unresolved";
    } else if (state.depth_limited) {
      state.trace.status = "depth_limited";
    } else {
      state.trace.status = "complete";
    }
    clock_trace_cache_[cache_key] = state.trace;
    return state.trace;
  }

  InstanceInfo build_instance(npiHandle module, bool nested) {
    InstanceInfo result;
    result.name = language_string(npiName, module);
    result.full_name = language_string(npiFullName, module);
    result.module = language_string(npiDefName, module);
    if (nested && !trace_rules_.empty() && !trace_rules_.count(result.module)) return result;
    result.file = language_string(npiFile, module);
    const NPI_INT32 line = npi_get(npiLineNo, module);
    if (line > 0) {
      result.line = static_cast<int>(line);
      result.has_line = true;
    }

    result.ports = collect_ports(module, result.full_name,
        interconnect_ && (trace_rules_.empty() || trace_rules_.count(result.module)));
    result.parameters = collect_parameters(module);
    std::string clock_port, reset_port;
    bool trace_required = false;
    if (trace_rules_.empty()) {
      clock_port = fallback_clk_port_;
      reset_port = fallback_rst_port_;
      trace_required = result.ports.count(clock_port) || result.ports.count(reset_port);
    } else {
      TraceRules::const_iterator rule = trace_rules_.find(result.module);
      if (rule != trace_rules_.end()) {
        clock_port = rule->second.first;
        reset_port = rule->second.second;
        trace_required = true;
      }
    }
    if (trace_required) {
      result.has_clock_trace = true;
      std::map<std::string, PortInfo>::const_iterator port =
          result.ports.find(clock_port);
      const PortInfo empty_port;
      const PortInfo& clk = port == result.ports.end() ? empty_port : port->second;
      port = result.ports.find(reset_port);
      const PortInfo& rst = port == result.ports.end() ? empty_port : port->second;
      // Leftovers from this boundary can later be claimed by an ancestor
      // row. Collect its target-specific CRG evidence without duplicating RS.
      std::set<std::string> targets;
      for (std::string b = current_position_; !b.empty();) {
        auto group = plan_.targets.find(NamePair(b, result.module));
        if (group != plan_.targets.end()) targets.insert(group->second.begin(), group->second.end());
        auto parent = boundary_parent_.find(b);
        if (parent == boundary_parent_.end()) break;
        b = parent->second;
      }
      if (!targets.empty()) {
        for (const auto& target : targets)
          result.target_traces[target] = std::make_pair(
              trace_clock(result.full_name, clock_port, clk, target),
              trace_clock(result.full_name, reset_port, rst, target, true));
        result.clock_trace = result.target_traces.begin()->second.first;
        result.reset_trace = result.target_traces.begin()->second.second;
      } else {
        result.clock_trace = trace_clock(result.full_name, clock_port, clk, "");
        result.reset_trace = trace_clock(result.full_name, reset_port, rst, "", true);
      }
      result.clk_sources = result.clock_trace.modules;
      result.has_reset_trace = true;
    }
    return result;
  }

  std::string fallback_clk_port_;
  std::string fallback_rst_port_;
  TraceRules trace_rules_;
  const DriverPlan& plan_;
  std::string current_position_;
  unsigned int trace_max_depth_;
  bool interconnect_;
  std::set<std::string> boundaries_;
  std::map<std::string, std::string> boundary_parent_;
  size_t scope_visits_ = 0;
  std::map<std::string, std::string> equivalent_nets_;
  std::map<std::string, std::map<std::string, std::string> > parent_wires_;
  std::set<std::string> warnings_;
  std::map<std::string, ClockTrace> clock_trace_cache_;
  std::map<std::string, std::map<std::string, PortInfo> > source_ports_;
};

void write_json_string(std::ostream& out, const std::string& value) {
  static const char kHex[] = "0123456789abcdef";
  out.put('"');
  for (std::string::const_iterator it = value.begin(); it != value.end(); ++it) {
    const unsigned char byte = static_cast<unsigned char>(*it);
    switch (byte) {
      case '"':
        out << "\\\"";
        break;
      case '\\':
        out << "\\\\";
        break;
      case '\b':
        out << "\\b";
        break;
      case '\f':
        out << "\\f";
        break;
      case '\n':
        out << "\\n";
        break;
      case '\r':
        out << "\\r";
        break;
      case '\t':
        out << "\\t";
        break;
      default:
        if (byte < 0x20) {
          out << "\\u00" << kHex[(byte >> 4) & 0x0f] << kHex[byte & 0x0f];
        } else {
          out.put(static_cast<char>(byte));
        }
        break;
    }
  }
  out.put('"');
}

bool write_hierarchy(const std::string& path, const HierarchyInfo& hierarchy,
                     const std::set<std::string>& notices,
                     std::string* error) {
  std::ofstream out(path.c_str(), std::ios::out | std::ios::binary |
                                      std::ios::trunc);
  if (!out) {
    *error = "cannot open output file: " + path;
    return false;
  }

  out << "{\n  \"schema\": \"rscheck.hierarchy\",\n"
      << "  \"schema_version\": 1,\n"
      << "  \"scopes\": [";
  if (!hierarchy.scopes.empty()) {
    out << '\n';
  }
  for (std::vector<HierarchyScopeInfo>::const_iterator scope =
           hierarchy.scopes.begin();
       scope != hierarchy.scopes.end(); ++scope) {
    out << "    {\"path\": ";
    write_json_string(out, scope->path);
    out << ", \"name\": ";
    write_json_string(out, scope->name);
    out << ", \"definition\": ";
    write_json_string(out, scope->definition);
    out << ", \"kind\": ";
    write_json_string(out, scope->kind);
    out << '}';
    std::vector<HierarchyScopeInfo>::const_iterator next = scope;
    ++next;
    out << (next == hierarchy.scopes.end() ? "\n" : ",\n");
  }
  out << "  ],\n  \"warnings\": [";
  if (!hierarchy.warnings.empty()) {
    out << '\n';
  }
  for (std::set<std::string>::const_iterator warning =
           hierarchy.warnings.begin();
       warning != hierarchy.warnings.end(); ++warning) {
    out << "    ";
    write_json_string(out, *warning);
    std::set<std::string>::const_iterator next = warning;
    ++next;
    out << (next == hierarchy.warnings.end() ? "\n" : ",\n");
  }
  out << "  ],\n  \"notices\": [";
  if (!notices.empty()) {
    out << '\n';
  }
  for (std::set<std::string>::const_iterator notice = notices.begin();
       notice != notices.end(); ++notice) {
    out << "    ";
    write_json_string(out, *notice);
    std::set<std::string>::const_iterator next = notice;
    ++next;
    out << (next == notices.end() ? "\n" : ",\n");
  }
  out << "  ],\n  \"truncated\": "
      << (hierarchy.truncated ? "true" : "false")
      << ",\n  \"visited_scopes\": " << hierarchy.visited_scopes << "\n}\n";
  out.flush();
  if (!out) {
    *error = "failed while writing output file: " + path;
    return false;
  }
  return true;
}

void write_string_array(std::ostream& out,
                        const std::vector<std::string>& values) {
  out << '[';
  for (std::vector<std::string>::const_iterator value = values.begin();
       value != values.end(); ++value) {
    if (value != values.begin()) {
      out << ", ";
    }
    write_json_string(out, *value);
  }
  out << ']';
}

void write_clock_trace(std::ostream& out, const ClockTrace& trace,
                       const std::string& indent, bool reset = false) {
  out << "{\n";
  out << indent << (reset ? "  \"reset_port\": " : "  \"clock_port\": ");
  write_json_string(out, trace.clock_port);
  out << ",\n" << indent << "  \"max_depth\": " << trace.max_depth;
  out << ",\n" << indent << "  \"strategy\": \"port_map_v1\", \"excluded_inputs\": []";
  out << ",\n" << indent << "  \"status\": ";
  write_json_string(out, trace.status);
  out << ",\n" << indent << "  \"modules\": [";
  if (!trace.modules.empty()) {
    out << '\n';
  }
  for (std::vector<DriverSource>::const_iterator module = trace.modules.begin();
       module != trace.modules.end(); ++module) {
    out << indent << "    {\"instance\": ";
    write_json_string(out, module->instance);
    out << ", \"module\": ";
    write_json_string(out, module->module);
    out << ", \"depth\": " << module->depth << ", \"path\": ";
    write_string_array(out, module->path);
    out << '}';
    std::vector<DriverSource>::const_iterator next = module;
    ++next;
    out << (next == trace.modules.end() ? "\n" : ",\n");
  }
  out << indent << "  ],\n" << indent << "  \"endpoints\": [";
  for (size_t i = 0; i < trace.endpoints.size(); ++i) {
    const ClockTrace::Endpoint& e = trace.endpoints[i];
    if (i) out << ", ";
    out << "{\"instance\": "; write_json_string(out, e.instance);
    out << ", \"port\": "; write_json_string(out, e.port);
    out << ", \"connection\": "; write_json_string(out, e.connection);
    out << ", \"depth\": " << e.depth << ", \"path\": "; write_string_array(out, e.path);
    out << '}';
  }
  out << "],\n" << indent << "  \"diagnostics\": [";
  if (!trace.diagnostics.empty()) {
    out << '\n';
  }
  for (std::vector<std::string>::const_iterator diagnostic =
           trace.diagnostics.begin();
       diagnostic != trace.diagnostics.end(); ++diagnostic) {
    out << indent << "    ";
    write_json_string(out, *diagnostic);
    std::vector<std::string>::const_iterator next = diagnostic;
    ++next;
    out << (next == trace.diagnostics.end() ? "\n" : ",\n");
  }
  out << indent << "  ]\n" << indent << '}';
}

void write_instance(std::ostream& out, const InstanceInfo& instance,
                    const std::string& indent) {
  out << indent << "{\n";
  out << indent << "  \"name\": ";
  write_json_string(out, instance.name);
  out << ",\n" << indent << "  \"full_name\": ";
  write_json_string(out, instance.full_name);
  out << ",\n" << indent << "  \"module\": ";
  write_json_string(out, instance.module);
  out << ",\n" << indent << "  \"file\": ";
  write_json_string(out, instance.file);
  out << ",\n" << indent << "  \"line\": ";
  if (instance.has_line) {
    out << instance.line;
  } else {
    out << "null";
  }

  out << ",\n" << indent << "  \"ports\": {";
  if (!instance.ports.empty()) {
    out << '\n';
  }
  std::map<std::string, PortInfo>::const_iterator port =
      instance.ports.begin();
  for (; port != instance.ports.end(); ++port) {
    out << indent << "    ";
    write_json_string(out, port->first);
    out << ": {\"connection\": ";
    write_json_string(out, port->second.connection);
    out << ", \"type\": ";
    write_json_string(out, port->second.object_type);
    const PortInfo& p = port->second;
    if (!p.link_kind.empty()) {
      out << ", \"link\": {\"kind\": "; write_json_string(out, p.link_kind);
      out << ", \"identity\": "; write_json_string(out, p.identity);
      out << ", \"interface_type\": "; write_json_string(out, p.interface_type);
      out << ", \"modport\": "; write_json_string(out, p.modport);
      out << ", \"direction\": ";
      write_json_string(out, p.link_kind == "signal" && p.has_direction ?
          (p.direction == npiInput ? "input" : p.direction == npiOutput ? "output" : "inout") : "unknown");
      out << '}';
    }
    if (p.member_scan) {
      out << ", \"member_pairs\": [";
      for (size_t j=0; j<p.member_pairs.size(); ++j) {
        const auto& edge=p.member_pairs[j];
        if (j) out << ", ";
        out << "{\"source\": "; write_json_string(out,edge.source);
        out << ", \"target\": "; write_json_string(out,edge.target);
        out << ", \"path\": [";
        for (size_t k=0; k<edge.path.size(); ++k) {
          if (k) out << ", ";
          write_json_string(out,edge.path[k]);
        }
        out << "]}";
      }
      out << ']';
    }
    out << '}';
    std::map<std::string, PortInfo>::const_iterator next = port;
    ++next;
    out << (next == instance.ports.end() ? "\n" : ",\n");
  }
  out << indent << "  },\n";

  out << indent << "  \"parameters\": {";
  if (!instance.parameters.empty()) {
    out << '\n';
  }
  std::map<std::string, ParameterInfo>::const_iterator parameter =
      instance.parameters.begin();
  for (; parameter != instance.parameters.end(); ++parameter) {
    out << indent << "    ";
    write_json_string(out, parameter->first);
    out << ": ";
    if (parameter->second.has_value) {
      write_json_string(out, parameter->second.value);
    } else {
      out << "null";
    }
    std::map<std::string, ParameterInfo>::const_iterator next = parameter;
    ++next;
    out << (next == instance.parameters.end() ? "\n" : ",\n");
  }
  out << indent << "  },\n";

  out << indent << "  \"clk_sources\": [";
  if (!instance.clk_sources.empty()) {
    out << '\n';
  }
  for (std::vector<DriverSource>::const_iterator source =
           instance.clk_sources.begin();
       source != instance.clk_sources.end(); ++source) {
    out << indent << "    {\"instance\": ";
    write_json_string(out, source->instance);
    out << ", \"module\": ";
    write_json_string(out, source->module);
    out << '}';
    std::vector<DriverSource>::const_iterator next = source;
    ++next;
    out << (next == instance.clk_sources.end() ? "\n" : ",\n");
  }
  out << indent << "  ],\n";
  out << indent << "  \"clock_trace\": ";
  if (instance.has_clock_trace) {
    write_clock_trace(out, instance.clock_trace, indent + "  ");
  } else out << "null";
  out << ",\n" << indent << "  \"reset_trace\": ";
  if (instance.has_reset_trace) write_clock_trace(out, instance.reset_trace, indent + "  ", true);
  else out << "null";
  out << ",\n" << indent << "  \"target_traces\": {";
  for (auto t = instance.target_traces.begin(); t != instance.target_traces.end(); ++t) {
    if (t != instance.target_traces.begin()) out << ", ";
    write_json_string(out, t->first);
    out << ": {\"clk\": "; write_clock_trace(out, t->second.first, indent + "    ");
    out << ", \"rst\": "; write_clock_trace(out, t->second.second, indent + "    ", true);
    out << "}";
  }
  out << "}";
  out << '\n';
  out << indent << '}';
}

bool write_inventory(const std::string& path, const PositionResults& positions,
                      const DriverPlan& plan,
                      const std::set<std::string>& warnings,
                      const std::set<std::string>& notices,
                      std::string* error) {
  std::ofstream out(path.c_str(), std::ios::out | std::ios::binary |
                                      std::ios::trunc);
  if (!out) {
    *error = "cannot open output file: " + path;
    return false;
  }

  out << "{\n  \"schema_version\": 4,\n  \"position_scope\": \"partitioned_subtree_v1\",\n  \"trace_cells\": [";
  for (auto rule = plan.inputs.begin(); rule != plan.inputs.end(); ++rule) {
    if (rule != plan.inputs.begin()) out << ", ";
    out << "{\"kind\": "; write_json_string(out, plan.kinds.at(rule->first.first));
    out << ", \"module\": "; write_json_string(out, rule->first.first);
    out << ", \"output\": "; write_json_string(out, rule->first.second);
    out << ", \"inputs\": "; write_string_array(out, std::vector<std::string>(rule->second.begin(), rule->second.end()));
    out << "}";
  }
  out << "],\n  \"positions\": {";
  if (!positions.empty()) {
    out << '\n';
  }
  for (PositionResults::const_iterator position = positions.begin();
       position != positions.end(); ++position) {
    out << "    ";
    write_json_string(out, position->first);
    out << ": {\n      \"found\": "
        << (position->second.found ? "true" : "false")
        << ",\n      \"child_positions\": ";
    write_string_array(out, std::vector<std::string>(position->second.child_positions.begin(),
                                                      position->second.child_positions.end()));
    out << ",\n      \"instances\": [";
    if (!position->second.instances.empty()) {
      out << '\n';
    }
    for (std::vector<InstanceInfo>::const_iterator instance =
             position->second.instances.begin();
         instance != position->second.instances.end(); ++instance) {
      write_instance(out, *instance, "        ");
      std::vector<InstanceInfo>::const_iterator next = instance;
      ++next;
      out << (next == position->second.instances.end() ? "\n" : ",\n");
    }
    out << "      ]\n    }";
    PositionResults::const_iterator next = position;
    ++next;
    out << (next == positions.end() ? "\n" : ",\n");
  }
  out << "  },\n  \"warnings\": [";
  if (!warnings.empty()) {
    out << '\n';
  }
  for (std::set<std::string>::const_iterator warning = warnings.begin();
       warning != warnings.end(); ++warning) {
    out << "    ";
    write_json_string(out, *warning);
    std::set<std::string>::const_iterator next = warning;
    ++next;
    out << (next == warnings.end() ? "\n" : ",\n");
  }
  out << "  ],\n  \"notices\": [";
  if (!notices.empty()) {
    out << '\n';
  }
  for (std::set<std::string>::const_iterator notice = notices.begin();
       notice != notices.end(); ++notice) {
    out << "    ";
    write_json_string(out, *notice);
    std::set<std::string>::const_iterator next = notice;
    ++next;
    out << (next == notices.end() ? "\n" : ",\n");
  }
  out << "  ]\n}\n";
  out.flush();
  if (!out) {
    *error = "failed while writing output file: " + path;
    return false;
  }
  return true;
}

int run(int argc, char** argv) {
  Options options;
  std::string error;
  if (!parse_options(argc, argv, &options, &error)) {
    std::cerr << "error[USAGE]: " << error << '\n';
    print_usage(std::cerr, argv[0]);
    return kUsageError;
  }
  if (options.show_help) {
    print_usage(std::cout, argv[0]);
    return kSuccess;
  }

  std::string elab_db_path;
  if (!validate_elab_db_path(options.elab_db_path, &elab_db_path, &error)) {
    std::cerr << "error[ELAB_DB]: " << error << '\n';
    return kElabDbError;
  }

  std::vector<std::string> positions;
  TraceRules trace_rules;
  DriverPlan driver_plan;
  if (!options.list_hierarchy) {
    if (!read_positions(options.positions_path, &positions, &error)) {
      std::cerr << "error[POSITIONS]: " << error << '\n';
      return kPositionsError;
    }
    if (!options.driver_plan_path.empty() && !read_driver_plan(options.driver_plan_path, &driver_plan, &error)) {
      std::cerr << "error[DRIVER_PLAN]: " << error << '\n';
      return kTraceRulesError;
    }
    if (!options.trace_rules_path.empty() &&
        !read_trace_rules(options.trace_rules_path, options.rst_port, &trace_rules, &error)) {
      std::cerr << "error[TRACE_RULES]: " << error << '\n';
      return kTraceRulesError;
    }
  }

  std::vector<std::string> npi_arguments;
  npi_arguments.push_back(argv[0]);
  npi_arguments.push_back("-elab");
  npi_arguments.push_back(elab_db_path);
  MutableArgv mutable_arguments(npi_arguments);
  int npi_argc = mutable_arguments.argc();
  char** npi_argv = mutable_arguments.argv();
  NpiSession session;
  if (!session.initialize(npi_argc, npi_argv)) {
    std::cerr << "error[NPI_INIT]: npi_init failed\n";
    return kNpiInitError;
  }
  std::set<std::string> notices;
  bool partial_load = false;
  if (!npi_load_design(npi_argc, npi_argv)) {
    const std::vector<std::string> top_instances = queryable_top_instances();
    if (top_instances.empty()) {
      std::cerr << "error[NPI_LOAD]: npi_load_design reported errors and no "
                   "top instance is queryable in Verdi elaborated KDB: "
                << elab_db_path << '\n';
      session.finish();
      return kNpiLoadError;
    }

    std::ostringstream message;
    message << "npi_load_design reported elaboration errors, but "
            << top_instances.size() << " top instance(s) remain queryable"
            << " (first: " << top_instances.front()
            << "); continuing with fail-closed "
            << (options.list_hierarchy ? "hierarchy enumeration"
                                       : "RTL evidence checks");
    notices.insert(message.str());
    partial_load = true;
    std::cerr << "warning[NPI_LOAD_PARTIAL]: " << message.str() << '\n';
  }

  if (options.list_hierarchy) {
    HierarchyCollector hierarchy_collector;
    HierarchyInfo hierarchy = hierarchy_collector.collect();
    if (partial_load) {
      hierarchy.truncated = true;
      hierarchy.warnings.insert(
          "hierarchy index may be incomplete because the elaborated KDB "
          "only partially loaded");
    }
    if (!session.finish()) {
      std::cerr << "error[NPI_END]: npi_end failed\n";
      return kNpiEndError;
    }
    if (!write_hierarchy(options.output_path, hierarchy, notices, &error)) {
      std::cerr << "error[OUTPUT]: " << error << '\n';
      return kOutputError;
    }
    return kSuccess;
  }

  // Parents must be visited before descendants to propagate their CRG targets.
  std::sort(positions.begin(), positions.end());
  Collector collector(options.clk_port, options.rst_port, trace_rules, driver_plan,
                      options.trace_max_depth, options.interconnect, positions);
  PositionResults results;
  results.reserve(positions.size());
  for (std::vector<std::string>::const_iterator position = positions.begin();
       position != positions.end(); ++position) {
    results.push_back(std::make_pair(*position,
                                     collector.collect_position(*position)));
  }

  collector.finish_member_links(&results);
  if (!session.finish()) {
    std::cerr << "error[NPI_END]: npi_end failed\n";
    return kNpiEndError;
  }
  if (!write_inventory(options.output_path, results, driver_plan, collector.warnings(),
                       notices, &error)) {
    std::cerr << "error[OUTPUT]: " << error << '\n';
    return kOutputError;
  }
  return kSuccess;
}

}  // namespace

int main(int argc, char** argv) {
  try {
    return run(argc, argv);
  } catch (const std::exception& error) {
    std::cerr << "error[INTERNAL]: " << error.what() << '\n';
  } catch (...) {
    std::cerr << "error[INTERNAL]: unknown exception\n";
  }
  return kInternalError;
}
