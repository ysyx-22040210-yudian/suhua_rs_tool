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

struct Options {
  std::string positions_path;
  std::string output_path;
  std::string clk_port;
  std::string rst_port;
  std::string trace_rules_path;
  std::string trace_max_depth_text;
  std::string elab_db_path;
  unsigned int trace_max_depth;
  bool show_help;

  Options() : trace_max_depth(kDefaultTraceMaxDepth), show_help(false) {}
};

struct PortInfo {
  std::string connection;
  std::string object_type;
  std::string full_name;
  int direction;
  bool has_direction;

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

  InstanceInfo() : line(0), has_line(false), has_clock_trace(false) {}
};

struct PositionInfo {
  bool found;
  std::vector<InstanceInfo> instances;

  PositionInfo() : found(false) {}
};

typedef std::vector<std::pair<std::string, PositionInfo> > PositionResults;
typedef std::map<std::string, std::string> TraceRules;

void print_usage(std::ostream& out, const char* program) {
  out << "Usage: " << program
      << " --positions <newline-file> --output <json-file>"
      << " --clk-port <name> --rst-port <name>"
      << " [--trace-rules <module-tab-clock-file>]"
      << " [--trace-max-depth <1..256>]"
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

  if (options->positions_path.empty() || options->output_path.empty() ||
      options->clk_port.empty() || options->rst_port.empty() ||
      options->elab_db_path.empty()) {
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

bool read_trace_rules(const std::string& path, TraceRules* rules,
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
    if (separator == std::string::npos ||
        line.find('\t', separator + 1) != std::string::npos) {
      std::ostringstream message;
      message << "trace rules line " << line_number
              << " must contain exactly module<TAB>clock_port";
      *error = message.str();
      return false;
    }
    const std::string module = trim_ascii(line.substr(0, separator));
    const std::string clock_port = trim_ascii(line.substr(separator + 1));
    if (module.empty() || clock_port.empty()) {
      std::ostringstream message;
      message << "trace rules line " << line_number
              << " has an empty module or clock port";
      *error = message.str();
      return false;
    }
    TraceRules::const_iterator existing = rules->find(module);
    if (existing != rules->end() && existing->second != clock_port) {
      std::ostringstream message;
      message << "trace rules define conflicting clock ports for module "
              << module;
      *error = message.str();
      return false;
    }
    (*rules)[module] = clock_port;
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

class Collector {
 public:
  Collector(const std::string& fallback_clk_port,
            const std::string& fallback_rst_port,
            const TraceRules& trace_rules, unsigned int trace_max_depth)
      : fallback_clk_port_(fallback_clk_port),
        fallback_rst_port_(fallback_rst_port),
        trace_rules_(trace_rules),
        trace_max_depth_(trace_max_depth) {}

  PositionInfo collect_position(const std::string& position) {
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
    collect_immediate_modules(scope, 0, &visited_scopes, &emitted_modules,
                              &result.instances);
    npi_release_handle(scope);

    std::sort(result.instances.begin(), result.instances.end(),
              instance_less);
    return result;
  }

  const std::set<std::string>& warnings() const { return warnings_; }

 private:
  static const unsigned int kMaxScopeDepth = 256;

  struct ModuleHit {
    std::string instance;
    std::string module;
  };

  struct ModuleFrontier {
    std::string instance;
    std::string module;
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
    std::map<std::string, unsigned int> best_object_depth;
    std::map<std::string, unsigned int> expanded_module_depth;
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
    return left.module < right.module;
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

  void collect_immediate_modules(
      npiHandle scope, unsigned int depth, std::set<std::string>* visited_scopes,
      std::set<std::string>* emitted_modules,
      std::vector<InstanceInfo>* instances) {
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
      const NPI_INT32 child_type = npi_get(npiType, child);
      if (child_type == npiModule) {
        const std::string key = language_object_key(child);
        if (emitted_modules->insert(key).second) {
          instances->push_back(build_instance(child));
        }
      } else if (child_type == npiGenScope) {
        // Generated scopes are transparent for the notion of an immediate
        // module child. Other scope types establish a hierarchy boundary.
        collect_immediate_modules(child, depth + 1, visited_scopes,
                                  emitted_modules, instances);
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

  PortInfo read_high_connection(npiHandle port,
                                TraceState* trace_state = NULL) {
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
      return result;
    }

    connection = resolve_ref_object(connection, trace_state);
    result.object_type = language_string(npiType, connection);
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
                  std::map<std::string, PortInfo>* ports) {
    if (port == NULL) {
      return;
    }
    const std::string name = language_string(npiName, port);
    if (name.empty()) {
      add_warning("NPI port is missing its name in module instance: " +
                  instance_full_name);
      return;
    }

    const PortInfo info = read_high_connection(port);
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
    if (existing->second.full_name.empty() && !info.full_name.empty()) {
      existing->second.full_name = info.full_name;
    }
    if (!existing->second.has_direction && info.has_direction) {
      existing->second.direction = info.direction;
      existing->second.has_direction = true;
    }
  }

  std::map<std::string, PortInfo> collect_ports(
      npiHandle module, const std::string& instance_full_name) {
    std::map<std::string, PortInfo> result;

    npiHandle iterator = npi_iterate(npiPort, module);
    if (iterator != NULL) {
      npiHandle port = NULL;
      while ((port = npi_scan(iterator)) != NULL) {
        merge_port(port, instance_full_name, &result);
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
      merge_port(*port, instance_full_name, &result);
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
      message << "clock trace exceeded the " << kMaxTraceObjectVisits
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

  void add_module_hit(npiNlHandle instance, std::vector<ModuleHit>* hits,
                      TraceState* state) {
    ModuleHit hit;
    hit.instance = netlist_string(npiNlFullName, instance);
    if (hit.instance.empty()) {
      hit.instance = netlist_string(npiNlName, instance);
    }
    hit.module = netlist_string(npiNlDefName, instance);
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
      message << "clock trace exceeded the " << kMaxTraceObjectStackDepth
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
      if (module_cell &&
          (direction == npiNlOutput || direction == npiNlInout)) {
        add_module_hit(instance, hits, state);
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
        add_module_hit(object, hits, state);
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
          unique.back().module != hit->module) {
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

    std::map<std::string, DriverSource>::iterator existing =
        state->modules_by_instance.find(source.instance);
    if (existing != state->modules_by_instance.end() &&
        (existing->second.depth < source.depth ||
         (existing->second.depth == source.depth &&
          !(source.path < existing->second.path)))) {
      return false;
    }
    state->modules_by_instance[source.instance] = source;
    frontier->instance = source.instance;
    frontier->module = source.module;
    frontier->depth = source.depth;
    frontier->path = source.path;
    return true;
  }

  void append_module_hits(const std::vector<ModuleHit>& hits,
                          unsigned int depth,
                          const std::vector<std::string>& parent_path,
                          std::deque<ModuleFrontier>* queue,
                          TraceState* state) {
    for (std::vector<ModuleHit>::const_iterator hit = hits.begin();
         hit != hits.end(); ++hit) {
      ModuleFrontier frontier;
      if (!record_module(*hit, depth, parent_path, state, &frontier)) {
        continue;
      }
      if (depth >= state->trace.max_depth) {
        state->depth_limited = true;
      } else {
        queue->push_back(frontier);
      }
    }
  }

  void merge_trace_language_port(npiHandle port,
                                 const std::string& instance_full_name,
                                 std::map<std::string, PortInfo>* ports,
                                 TraceState* state) {
    if (port == NULL) {
      return;
    }
    const std::string name = language_string(npiName, port);
    if (name.empty()) {
      mark_trace_unresolved(
          state, "NPI fallback port is missing its name in module instance: " +
                     instance_full_name);
      return;
    }
    const PortInfo info = read_high_connection(port, state);
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
    if (existing->second.full_name.empty() && !info.full_name.empty()) {
      existing->second.full_name = info.full_name;
    }
    if (!existing->second.has_direction && info.has_direction) {
      existing->second.direction = info.direction;
      existing->second.has_direction = true;
    }
  }

  bool trace_language_input_ports(const ModuleFrontier& current,
                                  std::deque<ModuleFrontier>* queue,
                                  TraceState* state) {
    std::map<std::string, PortInfo> ports;
    npiHandle module = npi_handle_by_name(current.instance.c_str(), NULL);
    if (module != NULL) {
      npiHandle iterator = npi_iterate(npiPort, module);
      if (iterator != NULL) {
        npiHandle port = NULL;
        while ((port = npi_scan(iterator)) != NULL) {
          merge_trace_language_port(port, current.instance, &ports, state);
          npi_release_handle(port);
        }
      }
      npi_release_handle(module);
    }

    std::vector<char> mutable_full_name(current.instance.begin(),
                                        current.instance.end());
    mutable_full_name.push_back('\0');
    hdlVec_t fallback_ports;
    npi_mod_inst_get_port(&mutable_full_name[0], fallback_ports);
    for (hdlVec_t::const_iterator port = fallback_ports.begin();
         port != fallback_ports.end(); ++port) {
      if (*port == NULL) {
        continue;
      }
      merge_trace_language_port(*port, current.instance, &ports, state);
      npi_release_handle(*port);
    }

    if (ports.empty()) {
      return false;
    }
    unsigned int unknown_directions = 0;
    for (std::map<std::string, PortInfo>::const_iterator port = ports.begin();
         port != ports.end(); ++port) {
      if (!port->second.has_direction) {
        ++unknown_directions;
        continue;
      }
      if (port->second.direction != npiInput || port->first == "clk" ||
          port->first == "rst_n") {
        continue;
      }
      npiNlHandle start = resolve_trace_start(current.instance, port->first,
                                              port->second);
      if (start == NULL) {
        mark_trace_unresolved(
            state, "NPI could not resolve intermediate input port: " +
                       current.instance + "." + port->first);
        continue;
      }
      std::vector<ModuleHit> hits;
      discover_upstream_modules(start, current.depth, &hits, state);
      npi_nl_release_handle(start);
      append_module_hits(hits, current.depth + 1, current.path, queue, state);
    }
    if (unknown_directions != 0) {
      std::ostringstream message;
      message << "NPI fallback returned " << unknown_directions
              << " intermediate port(s) with unknown direction for module: "
              << current.instance;
      mark_trace_unresolved(state, message.str());
    }
    return true;
  }

  void trace_netlist_input_relation(NPI_INT32 relation,
                                    npiNlHandle instance,
                                    const ModuleFrontier& current,
                                    unsigned int* scanned_ports,
                                    unsigned int* unknown_directions,
                                    std::deque<ModuleFrontier>* queue,
                                    TraceState* state) {
    npiNlHandle iterator = npi_nl_iterate(relation, instance);
    if (iterator == NULL) {
      return;
    }
    npiNlHandle port = NULL;
    while ((port = npi_nl_scan(iterator)) != NULL) {
      ++(*scanned_ports);
      const NPI_INT32 direction = npi_nl_get(npiNlDirection, port);
      if (direction != npiNlInput) {
        if (direction != npiNlOutput && direction != npiNlInout) {
          ++(*unknown_directions);
        }
        npi_nl_release_handle(port);
        continue;
      }
      const std::string name = netlist_string(npiNlName, port);
      if (name == "clk" || name == "rst_n") {
        npi_nl_release_handle(port);
        continue;
      }
      std::vector<ModuleHit> hits;
      discover_upstream_modules(port, current.depth, &hits, state);
      append_module_hits(hits, current.depth + 1, current.path, queue, state);
      npi_nl_release_handle(port);
    }
  }

  void expand_module_inputs(const ModuleFrontier& current,
                            std::deque<ModuleFrontier>* queue,
                            TraceState* state) {
    std::map<std::string, unsigned int>::iterator expanded =
        state->expanded_module_depth.find(current.instance);
    if (expanded != state->expanded_module_depth.end() &&
        expanded->second <= current.depth) {
      return;
    }
    state->expanded_module_depth[current.instance] = current.depth;

    unsigned int scanned_ports = 0;
    unsigned int unknown_directions = 0;
    npiNlHandle instance =
        npi_nl_handle_by_name(current.instance.c_str(), npiNlInst);
    if (instance == NULL) {
      instance =
          npi_nl_handle_by_name(current.instance.c_str(), npiNlUndefined);
    }
    if (instance != NULL) {
      trace_netlist_input_relation(
          npiNlInstPort, instance, current, &scanned_ports,
          &unknown_directions, queue, state);
      trace_netlist_input_relation(
          npiNlPseudoInstPort, instance, current, &scanned_ports,
          &unknown_directions, queue, state);
      npi_nl_release_handle(instance);
    }

    // Partial KDBs may expose a non-empty but incomplete Netlist port set.
    // Always merge the Language Model/L1 view so omitted input branches are
    // still traversed.  A complete Netlist view remains usable when that
    // fallback is unavailable.
    const bool fallback_available =
        trace_language_input_ports(current, queue, state);
    if (!fallback_available &&
        (scanned_ports == 0 || unknown_directions != 0)) {
      mark_trace_unresolved(
          state, "NPI could not enumerate input ports for upstream module: " +
                     current.instance);
    }
  }

  ClockTrace trace_clock(const std::string& instance_full_name,
                         const std::string& clock_port,
                         const PortInfo& port) {
    // The same leaf net name can occur in different hierarchy scopes when a
    // partial KDB cannot provide a canonical connection full name.
    std::string cache_key =
        instance_full_name + "\x1f" + clock_port + "\x1f";
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
          &state, "NPI Netlist could not resolve clock port: " +
                      instance_full_name + "." + clock_port);
    } else {
      std::vector<ModuleHit> hits;
      discover_upstream_modules(start, 0, &hits, &state);
      npi_nl_release_handle(start);

      std::deque<ModuleFrontier> queue;
      const std::vector<std::string> empty_path;
      append_module_hits(hits, 1, empty_path, &queue, &state);
      while (!queue.empty()) {
        const ModuleFrontier current = queue.front();
        queue.pop_front();
        expand_module_inputs(current, &queue, &state);
      }
      if (state.modules_by_instance.empty() && !state.unresolved) {
        add_trace_diagnostic(
            &state, "no upstream module output was resolved for clock port: " +
                        instance_full_name + "." + clock_port);
      }
    }

    for (std::map<std::string, DriverSource>::const_iterator source =
             state.modules_by_instance.begin();
         source != state.modules_by_instance.end(); ++source) {
      state.trace.modules.push_back(source->second);
    }
    std::sort(state.trace.modules.begin(), state.trace.modules.end(),
              source_less);
    std::sort(state.trace.diagnostics.begin(), state.trace.diagnostics.end());
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

  InstanceInfo build_instance(npiHandle module) {
    InstanceInfo result;
    result.name = language_string(npiName, module);
    result.full_name = language_string(npiFullName, module);
    result.module = language_string(npiDefName, module);
    result.file = language_string(npiFile, module);
    const NPI_INT32 line = npi_get(npiLineNo, module);
    if (line > 0) {
      result.line = static_cast<int>(line);
      result.has_line = true;
    }

    result.ports = collect_ports(module, result.full_name);
    result.parameters = collect_parameters(module);
    std::string clock_port;
    bool trace_required = false;
    if (trace_rules_.empty()) {
      clock_port = fallback_clk_port_;
      trace_required = result.ports.find(clock_port) != result.ports.end();
    } else {
      TraceRules::const_iterator rule = trace_rules_.find(result.module);
      if (rule != trace_rules_.end()) {
        clock_port = rule->second;
        trace_required = true;
      }
    }
    if (trace_required) {
      result.has_clock_trace = true;
      std::map<std::string, PortInfo>::const_iterator port =
          result.ports.find(clock_port);
      const PortInfo empty_port;
      result.clock_trace =
          trace_clock(result.full_name, clock_port,
                      port == result.ports.end() ? empty_port : port->second);
      result.clk_sources = result.clock_trace.modules;
    }
    return result;
  }

  std::string fallback_clk_port_;
  std::string fallback_rst_port_;
  TraceRules trace_rules_;
  unsigned int trace_max_depth_;
  std::set<std::string> warnings_;
  std::map<std::string, ClockTrace> clock_trace_cache_;
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
                       const std::string& indent) {
  out << "{\n";
  out << indent << "  \"clock_port\": ";
  write_json_string(out, trace.clock_port);
  out << ",\n" << indent << "  \"max_depth\": " << trace.max_depth;
  out << ",\n" << indent
      << "  \"excluded_inputs\": [\"clk\", \"rst_n\"]";
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
  out << indent << "  ],\n" << indent << "  \"diagnostics\": [";
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
    out << '\n';
  } else {
    out << "null\n";
  }
  out << indent << '}';
}

bool write_inventory(const std::string& path, const PositionResults& positions,
                      const std::set<std::string>& warnings,
                      const std::set<std::string>& notices,
                      std::string* error) {
  std::ofstream out(path.c_str(), std::ios::out | std::ios::binary |
                                      std::ios::trunc);
  if (!out) {
    *error = "cannot open output file: " + path;
    return false;
  }

  out << "{\n  \"schema_version\": 3,\n  \"positions\": {";
  if (!positions.empty()) {
    out << '\n';
  }
  for (PositionResults::const_iterator position = positions.begin();
       position != positions.end(); ++position) {
    out << "    ";
    write_json_string(out, position->first);
    out << ": {\n      \"found\": "
        << (position->second.found ? "true" : "false")
        << ",\n      \"instances\": [";
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
  if (!read_positions(options.positions_path, &positions, &error)) {
    std::cerr << "error[POSITIONS]: " << error << '\n';
    return kPositionsError;
  }
  TraceRules trace_rules;
  if (!options.trace_rules_path.empty() &&
      !read_trace_rules(options.trace_rules_path, &trace_rules, &error)) {
    std::cerr << "error[TRACE_RULES]: " << error << '\n';
    return kTraceRulesError;
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
            << "); continuing with fail-closed RTL evidence checks";
    notices.insert(message.str());
    std::cerr << "warning[NPI_LOAD_PARTIAL]: " << message.str() << '\n';
  }

  Collector collector(options.clk_port, options.rst_port, trace_rules,
                      options.trace_max_depth);
  PositionResults results;
  results.reserve(positions.size());
  for (std::vector<std::string>::const_iterator position = positions.begin();
       position != positions.end(); ++position) {
    results.push_back(std::make_pair(*position,
                                     collector.collect_position(*position)));
  }

  if (!session.finish()) {
    std::cerr << "error[NPI_END]: npi_end failed\n";
    return kNpiEndError;
  }
  if (!write_inventory(options.output_path, results, collector.warnings(),
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
