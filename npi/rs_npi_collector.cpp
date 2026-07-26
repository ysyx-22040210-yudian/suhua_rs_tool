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
#include "npi_L1.h"
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

class Collector {
 public:
  Collector(const std::string&, const std::string&) {}

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

  static bool instance_less(const InstanceInfo& left,
                            const InstanceInfo& right) {
    if (left.full_name != right.full_name) {
      return left.full_name < right.full_name;
    }
    return left.name < right.name;
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
    return result;
  }

  std::set<std::string> warnings_;
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

  out << indent << "  \"clk_sources\": []\n";
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

  out << "{\n  \"schema_version\": 2,\n  \"positions\": {";
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
