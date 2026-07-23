CXX ?= g++

VERDI_HOME ?=
NPI_PLATFORM ?= LINUX64

NPI_ROOT := $(VERDI_HOME)/share/NPI
NPI_INC ?= $(NPI_ROOT)/inc
NPI_LIB ?= $(NPI_ROOT)/lib/$(NPI_PLATFORM)

BUILD_DIR ?= build
TARGET ?= $(BUILD_DIR)/rs_npi_collector
SOURCE := rs_npi_collector.cpp
OBJECT := $(BUILD_DIR)/rs_npi_collector.o

# GNU Make tokenizes target and prerequisite names on whitespace. Fail early
# instead of producing misleading compile errors for unsupported build paths.
ifneq ($(words $(BUILD_DIR)),1)
$(error BUILD_DIR must not contain whitespace)
endif
ifneq ($(words $(TARGET)),1)
$(error TARGET must not contain whitespace)
endif
ifneq ($(words $(NPI_INC)),1)
$(error NPI_INC must not contain whitespace)
endif
ifneq ($(words $(NPI_LIB)),1)
$(error NPI_LIB must not contain whitespace)
endif

CXXFLAGS ?= -O2 -g
NPI_CXXFLAGS := -std=c++11 -Wall -Wextra -pedantic
NPI_LDFLAGS := -L"$(NPI_LIB)" -Wl,-rpath,"$(NPI_LIB)"
NPI_LDLIBS := -lNPI -ldl -lpthread -lrt -lz

.PHONY: all clean check-env

all: $(TARGET)

check-env:
	@test -n "$(VERDI_HOME)" || { \
		echo "error: VERDI_HOME is not set" >&2; exit 2; \
	}
	@test -d "$(NPI_INC)" || { \
		echo "error: NPI include directory not found: $(NPI_INC)" >&2; exit 2; \
	}
	@test -f "$(NPI_LIB)/libNPI.so" || { \
		echo "error: libNPI.so not found for NPI_PLATFORM=$(NPI_PLATFORM): $(NPI_LIB)" >&2; exit 2; \
	}

$(BUILD_DIR):
	mkdir -p "$@"

$(OBJECT): $(SOURCE) | check-env $(BUILD_DIR)
	$(CXX) $(CPPFLAGS) -I"$(NPI_INC)" $(CXXFLAGS) $(NPI_CXXFLAGS) -c "$<" -o "$@"

$(TARGET): $(OBJECT) | check-env $(BUILD_DIR)
	$(CXX) $(LDFLAGS) $(NPI_LDFLAGS) -o "$@" "$<" $(LDLIBS) $(NPI_LDLIBS)

clean:
	$(RM) "$(OBJECT)" "$(TARGET)"
	-rmdir "$(BUILD_DIR)"
