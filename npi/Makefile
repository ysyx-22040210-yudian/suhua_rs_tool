CXX ?= g++

VERDI_HOME ?=
NPI_PLATFORM ?= LINUX64

NPI_ROOT := $(VERDI_HOME)/share/NPI
NPI_PLATFORM_LOWER := $(shell printf '%s' "$(NPI_PLATFORM)" | tr '[:upper:]' '[:lower:]')
NPI_DEFAULT_LIB := $(NPI_ROOT)/lib/$(NPI_PLATFORM)
ifeq ($(wildcard $(NPI_DEFAULT_LIB)/libNPI.so),)
NPI_DEFAULT_LIB := $(NPI_ROOT)/lib/$(NPI_PLATFORM_LOWER)
endif
NPI_INC ?= $(NPI_ROOT)/inc
NPI_LIB ?= $(NPI_DEFAULT_LIB)
NPI_L1_INC ?= $(NPI_ROOT)/L1/C/inc
NPI_DEFAULT_L1_LIB := $(NPI_LIB)
ifeq ($(wildcard $(NPI_DEFAULT_L1_LIB)/libnpiL1.so),)
NPI_DEFAULT_L1_LIB := $(NPI_ROOT)/lib/$(NPI_PLATFORM_LOWER)
endif
NPI_L1_LIB ?= $(NPI_DEFAULT_L1_LIB)

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
ifneq ($(words $(NPI_L1_INC)),1)
$(error NPI_L1_INC must not contain whitespace)
endif
ifneq ($(words $(NPI_L1_LIB)),1)
$(error NPI_L1_LIB must not contain whitespace)
endif

CXXFLAGS ?= -O2 -g
NPI_CXXFLAGS := -std=c++11 -Wall -Wextra -pedantic
NPI_LDFLAGS := -L"$(NPI_LIB)" -L"$(NPI_L1_LIB)" \
	-Wl,-rpath,"$(NPI_LIB)" -Wl,-rpath,"$(NPI_L1_LIB)"
NPI_LDLIBS := -lnpiL1 -lNPI -ldl -lpthread -lrt -lz

.PHONY: all clean check-env

all: $(TARGET)

check-env:
	@test -n "$(VERDI_HOME)" || { \
		echo "error: VERDI_HOME is not set" >&2; exit 2; \
	}
	@test -d "$(NPI_INC)" || { \
		echo "error: NPI include directory not found: $(NPI_INC)" >&2; exit 2; \
	}
	@test -f "$(NPI_L1_INC)/npi_L1.h" || { \
		echo "error: NPI L1 header not found: $(NPI_L1_INC)/npi_L1.h" >&2; exit 2; \
	}
	@test -f "$(NPI_LIB)/libNPI.so" || { \
		echo "error: libNPI.so not found for NPI_PLATFORM=$(NPI_PLATFORM): $(NPI_LIB)" >&2; exit 2; \
	}
	@test -f "$(NPI_L1_LIB)/libnpiL1.so" || { \
		echo "error: libnpiL1.so not found: $(NPI_L1_LIB)" >&2; exit 2; \
	}

$(BUILD_DIR):
	mkdir -p "$@"

$(OBJECT): $(SOURCE) | check-env $(BUILD_DIR)
	$(CXX) $(CPPFLAGS) -I"$(NPI_INC)" -I"$(NPI_L1_INC)" \
		$(CXXFLAGS) $(NPI_CXXFLAGS) -c "$<" -o "$@"

$(TARGET): $(OBJECT) | check-env $(BUILD_DIR)
	$(CXX) $(LDFLAGS) $(NPI_LDFLAGS) -o "$@" "$<" $(LDLIBS) $(NPI_LDLIBS)

clean:
	$(RM) "$(OBJECT)" "$(TARGET)"
	-rmdir "$(BUILD_DIR)"
