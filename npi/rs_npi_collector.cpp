#include <algorithm>
#include <cerrno>
#include <cctype>
#include <cstring>
#include <cstdlib>
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
#include "npi_nl.h"

namespace {

enum ExitCode {
  kSuccess = 0,
  kUsageError = 2,
  kPositionsError = 3,
  kElabDbError = 4,
  kNpiInitError = 10,
  kNpiLoadError = 11,
  kNpiEndError = 12,
  kOutputError = 13,
  kInternalError = 14
};

struct Options {
  std::string positions_path;
  std::string output_path;
  std::string clk_port;
  std::string rst_port;
  std::string elab_db_path;
  bool show_help;

  Options() : show_help(false) {}
};

struct PortInfo {
  std::string connection;
  std::string object_type;
};

struct DriverSource {
  std::string instance;
  std::string module;
};

struct InstanceInfo {
  std::string name;
  std::string full_name;
  std::string module;
  std::string file;
  int line;
  bool has_line;
  std::map<std::string, PortInfo> ports;
  std::vector<DriverSource> clk_sources;

  InstanceInfo() : line(0), has_line(false) {}
};

struct PositionInfo {
  bool found;
  std::vector<InstanceInfo> instances;

  PositionInfo() : found(false) {}
};

typedef std::vector<std::pair<std::string, PositionInfo> > PositionResults;

void print_usage(std::ostream& out, const char* program) {
  out << "Usage: " << program
      << " --positions <newline-file> --output <json-file>"
      << " --clk-port <name> --rst-port <name>"
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
  Collector(const std::string& clk_port, const std::string& rst_port)
      : clk_port_(clk_port), rst_port_(rst_port) {}

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
  static const unsigned int kMaxDriverDepth = 512;

  struct TraceState {
    explicit TraceState(const std::string& signal_name)
        : signal(signal_name), depth_warning_emitted(false) {}

    std::string signal;
    std::set<std::string> visited;
    std::set<std::string> source_keys;
    std::vector<DriverSource> sources;
    bool depth_warning_emitted;
  };

  static bool instance_less(const InstanceInfo& left,
                            const InstanceInfo& right) {
    if (left.full_name != right.full_name) {
      return left.full_name < right.full_name;
    }
    return left.name < right.name;
  }

  static bool source_less(const DriverSource& left,
                          const DriverSource& right) {
    if (left.instance != right.instance) {
      return left.instance < right.instance;
    }
    return left.module < right.module;
  }

  void add_warning(const std::string& warning) { warnings_.insert(warning); }

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

  npiHandle resolve_ref_object(npiHandle object) {
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
      add_warning("npiRefObj resolution exceeded the depth limit at " +
                  language_string(npiFullName, object));
    }
    return object;
  }

  PortInfo read_high_connection(npiHandle port) {
    PortInfo result;
    npiHandle connection = npi_handle(npiHighConn, port);
    if (connection == NULL) {
      return result;
    }

    connection = resolve_ref_object(connection);
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

  std::map<std::string, PortInfo> collect_ports(npiHandle module) {
    std::map<std::string, PortInfo> result;

    npiHandle iterator = npi_iterate(npiPort, module);
    if (iterator == NULL) {
      return result;
    }

    npiHandle port = NULL;
    while ((port = npi_scan(iterator)) != NULL) {
      const std::string name = language_string(npiName, port);
      if (name == clk_port_ || name == rst_port_) {
        std::map<std::string, PortInfo>::const_iterator existing =
            result.find(name);
        if (existing == result.end()) {
          result.insert(std::make_pair(name, read_high_connection(port)));
        }
      }
      npi_release_handle(port);
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

  void add_module_source(npiNlHandle instance, TraceState* state) {
    DriverSource source;
    source.instance = netlist_string(npiNlFullName, instance);
    if (source.instance.empty()) {
      source.instance = netlist_string(npiNlName, instance);
    }
    source.module = netlist_string(npiNlDefName, instance);
    if (source.instance.empty() || source.module.empty()) {
      add_warning("NPI Netlist module driver is missing instance or definition "
                  "name for clock connection: " +
                  state->signal);
      return;
    }
    const std::string key = source.instance + "\x1f" + source.module;
    if (state->source_keys.insert(key).second) {
      state->sources.push_back(source);
    }
  }

  void follow_driver(npiNlHandle driver, unsigned int depth,
                     TraceState* state) {
    const NPI_INT32 type = npi_nl_get(npiNlType, driver);
    if (type == npiNlInstPort || type == npiNlPseudoInstPort) {
      const NPI_INT32 direction = npi_nl_get(npiNlDirection, driver);
      npiNlHandle instance = owning_instance(driver);
      const bool module_cell =
          instance != NULL &&
          npi_nl_get(npiNlCellType, instance) == npiNlModuleCell;

      if (module_cell &&
          (direction == npiNlOutput || direction == npiNlInout)) {
        add_module_source(instance, state);
      } else if (direction == npiNlInput) {
        // An input instance port is driven from the higher-level net.
        walk_drivers(driver, depth + 1, state);
      } else if (instance != NULL) {
        // For an inferred primitive output, move through the primitive to its
        // input pins before continuing upstream.
        walk_drivers(instance, depth + 1, state);
      } else {
        walk_drivers(driver, depth + 1, state);
      }

      if (instance != NULL) {
        npi_nl_release_handle(instance);
      }
      return;
    }

    if (type == npiNlInst) {
      if (npi_nl_get(npiNlCellType, driver) == npiNlModuleCell) {
        add_module_source(driver, state);
      } else {
        walk_drivers(driver, depth + 1, state);
      }
      return;
    }

    walk_drivers(driver, depth + 1, state);
  }

  void walk_drivers(npiNlHandle object, unsigned int depth, TraceState* state) {
    if (object == NULL) {
      return;
    }
    if (depth > kMaxDriverDepth) {
      if (!state->depth_warning_emitted) {
        add_warning("clock driver traversal exceeded the depth limit for " +
                    state->signal);
        state->depth_warning_emitted = true;
      }
      return;
    }
    if (!state->visited.insert(netlist_object_key(object)).second) {
      return;
    }

    npiNlHandle iterator = npi_nl_iterate(npiNlDriver, object);
    if (iterator == NULL) {
      return;
    }
    npiNlHandle driver = NULL;
    while ((driver = npi_nl_scan(iterator)) != NULL) {
      follow_driver(driver, depth, state);
      npi_nl_release_handle(driver);
    }
  }

  std::vector<DriverSource> trace_clock_sources(
      const std::string& connection) {
    std::map<std::string, std::vector<DriverSource> >::const_iterator cached =
        clock_source_cache_.find(connection);
    if (cached != clock_source_cache_.end()) {
      return cached->second;
    }

    std::vector<DriverSource> result;
    npiNlHandle start =
        npi_nl_handle_by_name(connection.c_str(), npiNlNet);
    if (start == NULL) {
      start = npi_nl_handle_by_name(connection.c_str(), npiNlUndefined);
    }
    if (start == NULL) {
      add_warning("NPI Netlist could not resolve clock connection: " +
                  connection);
      clock_source_cache_[connection] = result;
      return result;
    }

    TraceState state(connection);
    walk_drivers(start, 0, &state);
    npi_nl_release_handle(start);
    std::sort(state.sources.begin(), state.sources.end(), source_less);
    result.swap(state.sources);
    clock_source_cache_[connection] = result;
    return result;
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

    result.ports = collect_ports(module);
    std::map<std::string, PortInfo>::const_iterator clock =
        result.ports.find(clk_port_);
    if (clock != result.ports.end() && !clock->second.connection.empty() &&
        clock->second.object_type.find("Operation") == std::string::npos) {
      result.clk_sources = trace_clock_sources(clock->second.connection);
    }
    return result;
  }

  std::string clk_port_;
  std::string rst_port_;
  std::set<std::string> warnings_;
  std::map<std::string, std::vector<DriverSource> > clock_source_cache_;
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
  out << indent << "  ]\n";
  out << indent << '}';
}

bool write_inventory(const std::string& path, const PositionResults& positions,
                     const std::set<std::string>& warnings,
                     std::string* error) {
  std::ofstream out(path.c_str(), std::ios::out | std::ios::binary |
                                      std::ios::trunc);
  if (!out) {
    *error = "cannot open output file: " + path;
    return false;
  }

  out << "{\n  \"schema_version\": 1,\n  \"positions\": {";
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
  if (!npi_load_design(npi_argc, npi_argv)) {
    std::cerr << "error[NPI_LOAD]: npi_load_design failed for Verdi "
                 "elaborated KDB: "
              << elab_db_path << '\n';
    session.finish();
    return kNpiLoadError;
  }

  Collector collector(options.clk_port, options.rst_port);
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
                       &error)) {
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
