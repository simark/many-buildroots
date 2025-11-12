# Multi-Architecture GDB Build System

Automated framework for building GDB/gdbserver across multiple architectures using Buildroot toolchains.

## Python Build Tool (Recommended)

This project includes a Python-based build tool with proper dependency management using Poetry and Click.

### Installation

```bash
# Install dependencies
poetry install
```

### Quick Start

1. **Build toolchains** (one-time, takes a while):
   ```bash
   ./build-tool build-toolchains
   ```

2. **Build GDB with all toolchains**:
   ```bash
   ./build-tool build-gdb
   ```

3. **Use a toolchain interactively**:
   ```bash
   ./build-tool shell aarch64
   ```

4. **List available architectures**:
   ```bash
   ./build-tool list-archs
   ```

### Python Tool Usage

The `./build-tool` wrapper script invokes the Poetry-managed CLI tool. You can also use `poetry run build-tool` directly.

#### Initialize Build Directories

Initialize build directories without actually compiling. This is useful for inspecting or modifying configuration before building.

Initialize all toolchain build directories:
```bash
./build-tool init-toolchains
```

Initialize specific architectures:
```bash
./build-tool init-toolchains aarch64 arm
```

Initialize GDB build directories (requires toolchains to be built):
```bash
./build-tool init-gdb
```

Initialize GDB for specific architectures with custom configure options:
```bash
./build-tool init-gdb aarch64 arm -o "--enable-gdbserver --with-expat"
```

#### Build Commands

Build all toolchains:
```bash
./build-tool build-toolchains
```

Build specific architectures:
```bash
./build-tool build-toolchains aarch64 arm riscv
```

Clean build with custom parallel jobs:
```bash
./build-tool build-toolchains -c -j 8
```

Build GDB for all toolchains:
```bash
./build-tool build-gdb
```

Build GDB for specific architectures:
```bash
./build-tool build-gdb aarch64 arm
```

Custom GDB source and configure options:
```bash
./build-tool build-gdb -s /path/to/binutils-gdb -o "--enable-gdbserver --with-expat"
```

#### Using Toolchains Interactively

Spawn a subshell with toolchains in PATH:
```bash
# Shell with all toolchains available
./build-tool shell

# Shell with specific toolchains
./build-tool shell aarch64 arm
```

When you spawn a shell:
- All requested toolchain `bin/` directories are added to PATH
- If you specify a single architecture, environment variables are set:
  - `CROSS_COMPILE` - Cross-compilation prefix (e.g., `aarch64-buildroot-linux-gnu`)
  - `CC`, `CXX`, `AR`, `RANLIB` - Cross-compilation tools
- `BUILD_TOOL_ARCHS` - List of architectures available

Example usage:
```bash
$ ./build-tool shell aarch64
======================================================================
Build Tool Shell
======================================================================
Architectures: aarch64

Toolchain bin directories added to PATH:
  /data1/smarchi/many-buildroots/toolchains/aarch64/bin

Cross-compile prefixes:
  aarch64=aarch64-buildroot-linux-gnu

Environment variables set:
  CROSS_COMPILE=aarch64-buildroot-linux-gnu
  CC=aarch64-buildroot-linux-gnu-gcc
  CXX=aarch64-buildroot-linux-gnu-g++

Type 'exit' to leave this shell
======================================================================

$ aarch64-buildroot-linux-gnu-gcc --version
aarch64-buildroot-linux-gnu-gcc (Buildroot) ...

$ $CC -o hello hello.c

$ exit
Exited build tool shell
```

Options:
- `-j, --jobs N`: Number of parallel jobs (default: nproc)
- `-c, --clean`: Clean build directories before building
- `-s, --buildroot-src PATH` / `-s, --gdb-src PATH`: Custom source directory
- `-o, --configure-opts`: Additional configure options (GDB only)
- `-v, --verbose`: Show command output in real-time instead of logging to files
- `-k, --keep-going`: Continue with remaining architectures after failures (like `make -k`)
- `--help`: Show help for any command

#### Viewing Build Output

By default, build commands redirect output to log files for cleaner terminal output:
- Buildroot builds: `builds/<arch>/build.log`
- GDB configure: `gdb-builds/<arch>/configure.log`
- GDB build: `gdb-builds/<arch>/build.log`

To see output in real-time (useful for monitoring progress or debugging):
```bash
# Show output while building
./build-tool build-toolchains -v aarch64

# Show output during initialization
./build-tool init-gdb -v aarch64

# Verbose mode still logs to files while displaying output
```

When using `-v/--verbose`:
- Output is displayed in the terminal in real-time
- Output is also saved to log files (tee-like behavior)
- Useful for monitoring long builds or debugging configuration issues
- Error messages are immediately visible

#### Understanding Init vs Build

The tool provides separate commands for initialization and building:

**Init commands** prepare build directories without compiling:
- `init-toolchains`: Applies the defconfig, merges configuration fragments, and runs `olddefconfig`
- `init-gdb`: Runs the GDB `configure` script with appropriate cross-compilation settings

**Build commands** handle both initialization (if needed) and compilation:
- `build-toolchains`: Initializes if needed, then runs `make` to build the toolchain
- `build-gdb`: Initializes if needed, then runs `make all-gdb all-gdbserver`

If a build directory already exists with valid configuration, build commands will skip initialization unless `-c` (clean) is specified.

Use init commands when you want to:
- Inspect or modify configuration before building
- Set up build directories for manual building
- Test configuration changes without waiting for compilation

#### Error Handling for Multiple Architectures

When building or initializing multiple architectures, the tool stops at the first failure by default (similar to `make` without `-k`):

**Default behavior** (stop on first failure):
```bash
# If aarch64 build fails, arm and riscv won't be built
./build-tool build-toolchains aarch64 arm riscv
```

**Keep-going mode** (continue after failures):
```bash
# Build all three even if one fails
./build-tool build-toolchains -k aarch64 arm riscv

# Also works for other commands
./build-tool build-gdb -k
./build-tool init-toolchains -k aarch64 arm riscv
./build-tool init-gdb -k
```

When using `-k/--keep-going`:
- Failed architectures are marked as `FAILED` in the summary
- Remaining architectures continue to build/initialize
- Final summary shows which succeeded and which failed
- Useful for batch operations where you want to see all failures at once

## Shell Scripts (Legacy)

The original shell scripts are still available for reference:

### Quick Start (Shell Scripts)

1. **Build toolchains** (one-time, takes a while):
   ```bash
   ./build-toolchains.sh
   ```

2. **Build GDB with all toolchains**:
   ```bash
   GDB_SRC=~/src/gdb ./build-gdb.sh
   ```

## Detailed Usage

### Building Toolchains

Build all architectures:
```bash
./build-toolchains.sh
```

Build specific architectures:
```bash
./build-toolchains.sh aarch64 arm x86_64
```

Options:
- `-j N`: Number of parallel jobs (default: nproc)
- `-c`: Clean build directories first
- `-h`: Show help

### Building GDB

Build GDB for all available toolchains:
```bash
GDB_SRC=~/src/gdb ./build-gdb.sh
```

Build for specific architectures:
```bash
GDB_SRC=~/src/gdb ./build-gdb.sh aarch64 riscv64
```

Options:
- `-j N`: Number of parallel jobs (default: nproc)
- `-c`: Clean build directories first
- `-s PATH`: GDB source directory
- `-o OPTS`: Additional configure options (default: --enable-gdbserver)
- `-h`: Show help

The script builds only `all-gdb` and `all-gdbserver` targets (not the full binutils suite).

### Configuration

Edit `architectures.conf` to add/remove architectures:
```
arch_name:buildroot_defconfig
```

Edit `buildroot-fragment.conf` to modify Buildroot settings.

## Directory Structure

```
many-buildroots/
├── architectures.conf       # Architecture definitions
├── buildroot-fragment.conf  # Buildroot config overrides
├── build-toolchains.sh      # Toolchain build script
├── build-gdb.sh            # GDB build script
├── builds/                 # Buildroot build directories
│   ├── aarch64/
│   ├── arm/
│   └── ...
├── toolchains/             # Built toolchains
│   ├── aarch64/
│   ├── arm/
│   └── ...
├── gdb-builds/             # GDB build directories
│   ├── aarch64/
│   ├── arm/
│   └── ...
```

## Available Architectures

See `architectures.conf` for the full list. Common ones include:
- aarch64, arm
- x86_64, i686
- mips, mipsel, mips64, mips64el
- ppc, ppc64, ppc64le
- riscv32, riscv64
- sh4, sparc64, m68k

## Example Workflows

### Build everything from scratch
```bash
# List available architectures
./build-tool list-archs

# Build all toolchains
./build-tool build-toolchains -c

# Build GDB for all architectures
./build-tool build-gdb -c
```

### Build only specific architectures
```bash
# Build toolchains for a few architectures
./build-tool build-toolchains aarch64 arm riscv

# Build GDB for those architectures
./build-tool build-gdb aarch64 arm riscv
```

### Initialize without building (inspect/modify config first)
```bash
# Initialize toolchain build directories
./build-tool init-toolchains aarch64 arm

# Now you can manually edit builds/aarch64/.config or builds/arm/.config

# Build after inspecting/modifying configuration
./build-tool build-toolchains aarch64 arm
```

### Initialize GDB and build manually
```bash
# Initialize GDB build directory
./build-tool init-gdb aarch64

# Manually run make in the build directory
cd gdb-builds/aarch64
make -j8 all-gdb all-gdbserver
```

### Use toolchains for cross-compilation
```bash
# Spawn a shell with aarch64 toolchain
./build-tool shell aarch64

# Now you can use the cross-compilation tools
$ aarch64-buildroot-linux-gnu-gcc myapp.c -o myapp
$ file myapp
myapp: ELF 64-bit LSB executable, ARM aarch64, ...

# Or use environment variables (set for single-arch shells)
$ $CC --version
$ $CC -static myapp.c -o myapp-static

# Exit the shell when done
$ exit
```

### Work with multiple toolchains
```bash
# Shell with multiple toolchains available
./build-tool shell aarch64 arm riscv

# All toolchain binaries are in PATH
$ aarch64-buildroot-linux-gnu-gcc --version
$ arm-buildroot-linux-gnueabihf-gcc --version
$ riscv64-buildroot-linux-gnu-gcc --version
```

## Notes

- Toolchain builds are one-time and can be reused
- Buildroot downloads are shared in `~/src/buildroot/dl`
- Build logs are saved in each build directory
- Status summaries are in `toolchains/build-status.txt` and `gdb-builds/build-status.txt`
- GDB binaries are in `gdb-builds/<arch>/gdb/gdb` and `gdb-builds/<arch>/gdbserver/gdbserver`

### Build Performance

**ccache Support**: GDB builds automatically use [ccache](https://ccache.dev/) if available on your system:
- The tool detects ccache at build/init time
- If found, `CC` and `CXX` are automatically configured to use ccache
- Significantly speeds up rebuilds after configuration changes or GDB source updates
- When ccache is detected, you'll see `Using ccache: yes` in the output
- ccache is only used for GDB builds, not Buildroot toolchain builds

Example output when ccache is available:
```
Target triplet: aarch64-buildroot-linux-gnu
Cross prefix:   aarch64-buildroot-linux-gnu
Using ccache:   yes
Configuring GDB for aarch64...
```

To install ccache:
```bash
# Debian/Ubuntu
sudo apt install ccache

# Fedora/RHEL
sudo dnf install ccache

# macOS
brew install ccache
```

Check ccache statistics:
```bash
ccache -s
```

Clear ccache if needed:
```bash
ccache -C
```
