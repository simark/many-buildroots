#!/bin/bash
# Build GDB/gdbserver using the Buildroot toolchains

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLCHAINS_BASE="${SCRIPT_DIR}/toolchains"
GDB_SRC="${GDB_SRC:-$HOME/src/binutils-gdb}"
GDB_BUILD_BASE="${SCRIPT_DIR}/gdb-builds"

# Parse command line options
PARALLEL_JOBS="${PARALLEL_JOBS:-$(nproc)}"
ARCHS_TO_BUILD=""
CLEAN_BUILD=0
CONFIGURE_OPTS="--enable-gdbserver"

usage() {
    echo "Usage: $0 [OPTIONS] [ARCH...]"
    echo ""
    echo "Build GDB/gdbserver using Buildroot toolchains"
    echo ""
    echo "Options:"
    echo "  -j N          Number of parallel make jobs (default: nproc)"
    echo "  -c            Clean build directories before building"
    echo "  -s PATH       GDB source directory (default: ~/src/binutils-gdb)"
    echo "  -o OPTS       Additional configure options (default: --enable-gdbserver)"
    echo "  -h            Show this help"
    echo ""
    echo "Arguments:"
    echo "  ARCH          Specific architectures to build (default: all available toolchains)"
    echo ""
    echo "Environment variables:"
    echo "  GDB_SRC       Path to GDB source code"
    exit 0
}

while getopts "j:cs:o:h" opt; do
    case $opt in
        j) PARALLEL_JOBS="$OPTARG" ;;
        c) CLEAN_BUILD=1 ;;
        s) GDB_SRC="$OPTARG" ;;
        o) CONFIGURE_OPTS="$OPTARG" ;;
        h) usage ;;
        *) usage ;;
    esac
done
shift $((OPTIND-1))

# Verify GDB source exists
if [ ! -d "$GDB_SRC" ]; then
    echo "ERROR: GDB source directory not found: $GDB_SRC"
    echo "Set GDB_SRC environment variable or use -s option"
    exit 1
fi

# Get list of architectures to build
if [ $# -gt 0 ]; then
    ARCHS_TO_BUILD="$*"
else
    # Use all architectures that have toolchains built
    ARCHS_TO_BUILD=$(ls -1 "$TOOLCHAINS_BASE" 2>/dev/null || echo "")
    if [ -z "$ARCHS_TO_BUILD" ]; then
        echo "ERROR: No toolchains found in $TOOLCHAINS_BASE"
        echo "Run build-toolchains.sh first"
        exit 1
    fi
fi

echo "======================================"
echo "GDB Build Script"
echo "======================================"
echo "GDB source:       $GDB_SRC"
echo "Toolchains base:  $TOOLCHAINS_BASE"
echo "Build directory:  $GDB_BUILD_BASE"
echo "Parallel jobs:    $PARALLEL_JOBS"
echo "Configure opts:   $CONFIGURE_OPTS"
echo "Architectures:    $(echo "$ARCHS_TO_BUILD" | wc -w)"
echo ""

mkdir -p "$GDB_BUILD_BASE"

# Build for each architecture
for arch in $ARCHS_TO_BUILD; do
    echo "======================================"
    echo "Building GDB for: $arch"
    echo "======================================"
    
    TOOLCHAIN_DIR="${TOOLCHAINS_BASE}/${arch}"
    if [ ! -d "$TOOLCHAIN_DIR" ]; then
        echo "ERROR: Toolchain not found for $arch: $TOOLCHAIN_DIR"
        echo "Skipping..."
        continue
    fi
    
    # Find the cross-compile prefix
    CROSS_COMPILE=""
    for gcc_path in "$TOOLCHAIN_DIR/bin/"*-gcc; do
        if [ -f "$gcc_path" ]; then
            CROSS_COMPILE=$(basename "$gcc_path" | sed 's/-gcc$//')
            break
        fi
    done
    
    if [ -z "$CROSS_COMPILE" ]; then
        echo "ERROR: Could not find cross-compiler in $TOOLCHAIN_DIR/bin/"
        echo "Skipping..."
        continue
    fi
    
    BUILD_DIR="${GDB_BUILD_BASE}/${arch}"
    
    # Clean if requested
    if [ $CLEAN_BUILD -eq 1 ]; then
        echo "Cleaning build directory for $arch..."
        rm -rf "$BUILD_DIR"
    fi
    
    mkdir -p "$BUILD_DIR"
    
    # Setup environment
    export PATH="${TOOLCHAIN_DIR}/bin:$PATH"
    export CROSS_COMPILE="${CROSS_COMPILE}"
    export CC="${CROSS_COMPILE}-gcc"
    export CXX="${CROSS_COMPILE}-g++"
    export AR="${CROSS_COMPILE}-ar"
    export RANLIB="${CROSS_COMPILE}-ranlib"
    
    # Get target triplet
    TARGET=$("${CC}" -dumpmachine)
    echo "Target triplet: $TARGET"
    echo "Cross prefix:   $CROSS_COMPILE"
    
    # Configure
    echo "Configuring GDB for $arch..."
    cd "$BUILD_DIR"
    # shellcheck disable=SC2086
    if ! "$GDB_SRC/configure" \
        --host="$TARGET" \
        --target="$TARGET" \
        $CONFIGURE_OPTS \
        2>&1 | tee configure.log; then
        echo "FAILED: Configuration for $arch failed"
        echo "$arch: CONFIGURE_FAILED" >> "$GDB_BUILD_BASE/build-status.txt"
        continue
    fi
    
    # Build
    echo "Building GDB and gdbserver for $arch..."
    if make -j"$PARALLEL_JOBS" all-gdb all-gdbserver 2>&1 | tee build.log; then
        echo "SUCCESS: GDB for $arch built successfully"
        
        # Show what was built
        echo "Built executables:"
        find . -name gdb -o -name gdbserver 2>/dev/null | head -10 || true
        
        echo "$arch: SUCCESS" >> "$GDB_BUILD_BASE/build-status.txt"
    else
        echo "FAILED: Build for $arch failed"
        echo "$arch: BUILD_FAILED" >> "$GDB_BUILD_BASE/build-status.txt"
    fi
    
    echo ""
done

echo "======================================"
echo "GDB Build Summary"
echo "======================================"
cat "$GDB_BUILD_BASE/build-status.txt" 2>/dev/null || echo "No builds completed"
echo ""
echo "GDB builds are in: $GDB_BUILD_BASE"
