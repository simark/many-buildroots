# AGENTS.md

AI Assistant Guide for the Multi-Architecture GDB Build System

## Project Overview

This project automates building GDB and gdbserver across multiple CPU architectures using Buildroot-generated cross-compilation toolchains.

**Main Use Case**: Build and test GDB for many different architectures (aarch64, arm, x86_64, mips, riscv, etc.) without manually setting up each toolchain.

## Key Files

- `build_tool/cli.py` - Main Python CLI implementation
- `architectures.conf` - Architecture definitions (arch_name:buildroot_defconfig)
- `buildroot-fragment.conf` - Buildroot configuration overrides
- `external-toolchains.conf` - External Bootlin toolchain mappings
- `arch-buildroot-options.conf` - Architecture-specific build options
- `build-tool` - Wrapper script that calls `poetry run build-tool`

## Common Commands

```bash
# List available architectures
./build-tool list-archs

# Build toolchains (one-time, slow)
./build-tool build-toolchains aarch64 arm

# Build GDB for specific architectures
./build-tool build-gdb aarch64 arm

# Open shell with toolchain in PATH
./build-tool shell aarch64
```

## Development Workflow

1. **Adding a new architecture**: Add line to `architectures.conf` with format `arch_name:defconfig_name`
2. **Modifying Buildroot config**: Edit `buildroot-fragment.conf` (applies to all architectures)
3. **Architecture-specific options**: Add to `arch-buildroot-options.conf`
4. **Testing changes**: Use `init-toolchains` or `init-gdb` to set up build directories without compiling
5. **Code formatting**: Run `poetry run black build_tool`, `poetry run flake8 build_tool` and `poetry run isort build_tool`

## Directory Structure

```
many-buildroots/
├── builds/           # Buildroot build directories
├── toolchains/       # Compiled toolchains (output)
├── gdb-builds/       # GDB build directories
└── build_tool/       # Python CLI source code
```

## Tips

- Toolchain builds are one-time and can be reused across GDB rebuilds
- Use `-k/--keep-going` flag to continue building after failures
- Build logs are in `builds/<arch>/build.log` and `gdb-builds/<arch>/build.log`
- The tool automatically uses ccache if available for faster GDB rebuilds
- Use `-v/--verbose` to see real-time output instead of logging to files
