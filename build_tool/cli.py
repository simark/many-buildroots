"""
CLI for building Buildroot toolchains and GDB across multiple architectures.
"""

import multiprocessing
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import click
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)


def _echo(
    msg: str, console: Optional[Console] = None, err: bool = False, **kwargs
) -> None:
    """Print using Rich console if available, otherwise click.echo."""
    if console:
        console.print(msg, **kwargs)
    else:
        click.echo(msg, err=err)


class BuildConfig:
    """Configuration and paths for the build system."""

    def __init__(self, script_dir: Path):
        self.script_dir = script_dir
        self.arch_conf = script_dir / "architectures.conf"
        self.br_fragment = script_dir / "buildroot-fragment.conf"
        self.output_base = script_dir / "toolchains"
        self.build_base = script_dir / "builds"
        self.gdb_build_base = script_dir / "gdb-builds"

        # Default source locations (can be overridden)
        self.buildroot_src = os.environ.get(
            "BUILDROOT_SRC", str(Path.home() / "src" / "buildroot")
        )
        self.gdb_src = os.environ.get(
            "GDB_SRC", str(Path.home() / "src" / "binutils-gdb")
        )

    def load_architectures(self) -> Dict[str, str]:
        """Load architecture configurations from architectures.conf."""
        archs = {}
        if not self.arch_conf.exists():
            click.echo(
                f"ERROR: Architecture config not found: {self.arch_conf}", err=True
            )
            sys.exit(1)

        with open(self.arch_conf, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(":", 1)
                if len(parts) == 2:
                    archs[parts[0]] = parts[1]

        return archs

    def get_available_toolchains(self) -> List[str]:
        """Get list of architectures with built toolchains."""
        if not self.output_base.exists():
            return []
        return [d.name for d in self.output_base.iterdir() if d.is_dir()]


def run_command(
    cmd: List[str],
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    log_file: Optional[Path] = None,
    verbose: bool = False,
    console: Optional[Console] = None,
) -> bool:
    """
    Run a command and return True if successful.

    Args:
        cmd: Command and arguments to run
        cwd: Working directory
        env: Environment variables
        log_file: Path to log file for output
        verbose: If True, show output to terminal; if False, only log to file
        console: Rich console for coordinated output with progress bars

    Returns:
        True if command succeeded, False otherwise
    """
    try:
        if verbose:
            # Show output to terminal and optionally log to file
            if log_file:
                # Use tee-like behavior: show on terminal and write to file
                # Capture stdout and stderr separately to display stderr in yellow
                import selectors

                with open(log_file, "w") as f:
                    process = subprocess.Popen(
                        cmd,
                        cwd=cwd,
                        env=env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        bufsize=1,
                    )
                    assert process.stdout is not None
                    assert process.stderr is not None

                    # Use selectors to read from both stdout and stderr
                    sel = selectors.DefaultSelector()
                    sel.register(process.stdout, selectors.EVENT_READ)
                    sel.register(process.stderr, selectors.EVENT_READ)

                    while sel.get_map():
                        events = sel.select()
                        for key, _ in events:
                            line = key.fileobj.readline()  # type: ignore
                            if not line:
                                sel.unregister(key.fileobj)
                                continue

                            if key.fileobj is process.stdout:
                                if console:
                                    console.print(line, end="")
                                else:
                                    print(line, end="")
                                f.write(line)
                            else:  # stderr
                                if console:
                                    console.print(
                                        line, end="", style="yellow"
                                    )
                                else:
                                    print(line, end="", file=sys.stderr)
                                f.write(line)

                    process.wait()
                    result_code = process.returncode
            else:
                # Just show on terminal (can't easily capture without log file)
                result = subprocess.run(cmd, cwd=cwd, env=env, check=False)
                result_code = result.returncode
        else:
            # Quiet mode: redirect to log file or suppress output
            if log_file:
                with open(log_file, "w") as f:
                    result = subprocess.run(
                        cmd,
                        cwd=cwd,
                        env=env,
                        stdout=f,
                        stderr=subprocess.STDOUT,
                        text=True,
                    )
                    result_code = result.returncode
            else:
                result = subprocess.run(cmd, cwd=cwd, env=env, check=False)
                result_code = result.returncode

        return result_code == 0
    except Exception as e:
        _echo(f"ERROR running command: {e}", console, err=True)
        return False


def load_external_toolchains(config: BuildConfig) -> Dict[str, str]:
    """
    Load external toolchain mappings from external-toolchains.conf

    Returns:
        Dictionary mapping architecture names to Bootlin toolchain config names
    """
    external_tc_file = config.script_dir / "external-toolchains.conf"
    if not external_tc_file.exists():
        return {}

    mappings = {}
    with open(external_tc_file, "r") as f:
        for line in f:
            line = line.strip()
            # Skip comments and empty lines
            if not line or line.startswith("#"):
                continue

            if ":" in line:
                arch, toolchain = line.split(":", 1)
                arch = arch.strip()
                toolchain = toolchain.strip()
                if toolchain:  # Only add if toolchain is specified
                    mappings[arch] = toolchain

    return mappings


def create_external_toolchain_fragment(
    arch: str, toolchain_config: str, fragment_path: Path
) -> None:
    """
    Create a Buildroot config fragment for using an external toolchain

    Args:
        arch: Architecture name
        toolchain_config: Bootlin toolchain config name
            (without BR2_TOOLCHAIN_EXTERNAL_BOOTLIN_ prefix)
        fragment_path: Path where to write the fragment
    """
    with open(fragment_path, "w") as f:
        f.write(f"# External toolchain configuration for {arch}\n")
        f.write("BR2_TOOLCHAIN_EXTERNAL=y\n")
        f.write("BR2_TOOLCHAIN_EXTERNAL_BOOTLIN=y\n")
        f.write(f"BR2_TOOLCHAIN_EXTERNAL_BOOTLIN_{toolchain_config}=y\n")


def load_arch_buildroot_options(config: BuildConfig) -> Dict[str, List[str]]:
    """
    Load architecture-specific Buildroot options from arch-buildroot-options.conf

    Returns:
        Dictionary mapping architecture names to lists of config options
    """
    options_file = config.script_dir / "arch-buildroot-options.conf"
    if not options_file.exists():
        return {}

    mappings: Dict[str, List[str]] = {}
    with open(options_file, "r") as f:
        for line in f:
            line = line.strip()
            # Skip comments and empty lines
            if not line or line.startswith("#"):
                continue

            if ":" in line:
                arch, options_str = line.split(":", 1)
                arch = arch.strip()
                options_str = options_str.strip()
                if options_str:  # Only add if options are specified
                    # Split by comma for multiple options
                    options = [opt.strip() for opt in options_str.split(",")]
                    mappings[arch] = options

    return mappings


def init_single_toolchain(
    arch: str,
    defconfig: str,
    config: BuildConfig,
    clean: bool,
    verbose: bool = False,
    console: Optional[Console] = None,
) -> bool:
    """
    Initialize a single Buildroot build directory (without building).

    Args:
        arch: Architecture name
        defconfig: Buildroot defconfig to use
        config: Build configuration
        clean: Whether to clean before initializing
        verbose: Whether to show command output
        console: Rich console for coordinated output with progress bars

    Returns:
        True if initialization succeeded, False otherwise
    """
    build_dir = config.build_base / arch
    output_dir = config.output_base / arch

    # Clean if requested
    if clean and build_dir.exists():
        _echo(f"Cleaning build directory for {arch}...", console)
        shutil.rmtree(build_dir)

    # Create build directory
    build_dir.mkdir(parents=True, exist_ok=True)

    # Configure buildroot
    _echo(f"Configuring buildroot with {defconfig}...", console)
    if not run_command(
        ["make", "-C", config.buildroot_src, f"O={build_dir}", defconfig],
        verbose=verbose,
        console=console,
    ):
        _echo(f"ERROR: Failed to configure buildroot for {arch}", console, err=True)
        return False

    # Apply fragment
    _echo("Applying configuration fragment...", console)
    config_file = build_dir / ".config"
    with open(config.br_fragment, "r") as src:
        with open(config_file, "a") as dst:
            dst.write(src.read())

    # Check for external toolchain configuration
    external_toolchains = load_external_toolchains(config)
    if arch in external_toolchains:
        toolchain_config = external_toolchains[arch]
        _echo(f"Using external Bootlin toolchain: {toolchain_config}", console)

        # Create temporary fragment for external toolchain
        temp_fragment = build_dir / "external-toolchain.fragment"
        create_external_toolchain_fragment(arch, toolchain_config, temp_fragment)

        # Apply external toolchain fragment
        with open(temp_fragment, "r") as src:
            with open(config_file, "a") as dst:
                dst.write("\n")
                dst.write(src.read())

        # Clean up temporary fragment
        temp_fragment.unlink()

    # Apply architecture-specific Buildroot options
    arch_options = load_arch_buildroot_options(config)
    if arch in arch_options:
        options = arch_options[arch]
        _echo(f"Applying architecture-specific options: {', '.join(options)}", console)
        with open(config_file, "a") as dst:
            dst.write(f"\n# Architecture-specific options for {arch}\n")
            for option in options:
                dst.write(f"{option}\n")

    # Set output directory in config
    with open(config_file, "r") as f:
        lines = f.readlines()

    with open(config_file, "w") as f:
        for line in lines:
            if line.startswith("BR2_HOST_DIR="):
                f.write(f'BR2_HOST_DIR="{output_dir}"\n')
            else:
                f.write(line)

    # Run olddefconfig to merge
    _echo("Running olddefconfig to merge configuration...", console)
    if not run_command(
        ["make", "olddefconfig"], cwd=build_dir, verbose=verbose, console=console
    ):
        _echo(f"ERROR: Failed to run olddefconfig for {arch}", console, err=True)
        return False

    _echo(f"SUCCESS: Build directory initialized for {arch}", console)
    _echo(f"Build directory: {build_dir}", console)
    return True


def build_single_toolchain(
    arch: str,
    defconfig: str,
    config: BuildConfig,
    parallel_jobs: int,
    clean: bool,
    verbose: bool = False,
    console: Optional[Console] = None,
) -> bool:
    """
    Build a single Buildroot toolchain.

    Args:
        arch: Architecture name
        defconfig: Buildroot defconfig to use
        config: Build configuration
        parallel_jobs: Number of parallel make jobs
        clean: Whether to clean before building
        verbose: Whether to show command output
        console: Rich console for coordinated output with progress bars

    Returns:
        True if build succeeded, False otherwise
    """
    _echo("=" * 50, console)
    _echo(f"Building toolchain for: {arch}", console)
    _echo("=" * 50, console)

    build_dir = config.build_base / arch
    output_dir = config.output_base / arch

    # Initialize if needed or if clean was requested
    if clean or not (build_dir / ".config").exists():
        if not init_single_toolchain(arch, defconfig, config, clean, verbose, console):
            return False
    else:
        _echo(f"Using existing configuration in {build_dir}", console)

    # Build
    _echo(
        f"Building toolchain and target libraries for {arch} "
        "(this may take a while)...",
        console,
    )
    log_file = build_dir / "build.log"
    success = run_command(
        ["make", f"-j{parallel_jobs}"],
        cwd=build_dir,
        log_file=log_file,
        verbose=verbose,
        console=console,
    )

    if success:
        _echo(f"SUCCESS: Toolchain for {arch} built successfully", console)
        _echo(f"Toolchain location: {output_dir}", console)
    else:
        _echo(f"FAILED: Toolchain build for {arch} failed", console, err=True)
        if not verbose:
            _echo(f"Check log: {log_file}", console)

    return success


def find_cross_compile_prefix(toolchain_dir: Path) -> Optional[str]:
    """Find the cross-compile prefix for a toolchain."""
    bin_dir = toolchain_dir / "bin"
    if not bin_dir.exists():
        return None

    for gcc_path in bin_dir.glob("*-gcc"):
        if gcc_path.is_file():
            return gcc_path.name.replace("-gcc", "")

    return None


def check_ccache_available() -> bool:
    """Check if ccache is available in the system."""
    try:
        result = subprocess.run(
            ["which", "ccache"], capture_output=True, text=True, check=False
        )
        return result.returncode == 0
    except Exception:
        return False


def init_single_gdb(
    arch: str,
    config: BuildConfig,
    clean: bool,
    configure_opts: str,
    verbose: bool = False,
    console: Optional[Console] = None,
) -> Optional[Dict[str, Any]]:
    """
    Initialize a single GDB build directory (without building).

    Args:
        arch: Architecture name
        config: Build configuration
        clean: Whether to clean before initializing
        configure_opts: Additional configure options
        verbose: Whether to show command output
        console: Rich console for coordinated output with progress bars

    Returns:
        Dictionary with build_dir and env if successful, None otherwise
    """
    toolchain_dir = config.output_base / arch
    if not toolchain_dir.exists():
        _echo(
            f"ERROR: Toolchain not found for {arch}: {toolchain_dir}", console, err=True
        )
        _echo("Skipping...", console)
        return None

    # Find cross-compile prefix
    cross_compile = find_cross_compile_prefix(toolchain_dir)
    if not cross_compile:
        _echo(
            f"ERROR: Could not find cross-compiler in {toolchain_dir}/bin/",
            console,
            err=True,
        )
        _echo("Skipping...", console)
        return None

    build_dir = config.gdb_build_base / arch

    # Clean if requested
    if clean and build_dir.exists():
        _echo(f"Cleaning build directory for {arch}...", console)
        shutil.rmtree(build_dir)

    # Create build directory
    build_dir.mkdir(parents=True, exist_ok=True)

    # Setup environment
    env = os.environ.copy()
    env["PATH"] = f"{toolchain_dir}/bin:{env['PATH']}"
    env["CROSS_COMPILE"] = cross_compile

    # Check for ccache and configure compiler commands
    use_ccache = check_ccache_available()
    if use_ccache:
        env["CC"] = f"ccache {cross_compile}-gcc"
        env["CXX"] = f"ccache {cross_compile}-g++"
    else:
        env["CC"] = f"{cross_compile}-gcc"
        env["CXX"] = f"{cross_compile}-g++"

    env["AR"] = f"{cross_compile}-ar"
    env["RANLIB"] = f"{cross_compile}-ranlib"

    # Get target triplet (using the actual compiler, not ccache wrapper)
    try:
        result = subprocess.run(
            [f"{cross_compile}-gcc", "-dumpmachine"],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
        target = result.stdout.strip()
    except Exception as e:
        _echo(f"ERROR: Failed to get target triplet: {e}", console, err=True)
        return None

    # Some architectures need libatomic for atomic operations
    # Empirically determined via test-atomic.cpp/test-atomic.sh
    # Add -latomic to LDFLAGS for architectures without native atomic support
    archs_needing_libatomic = ["microblaze", "sparc"]
    if arch in archs_needing_libatomic:
        existing_ldflags = env.get("LDFLAGS", "")
        env["LDFLAGS"] = f"{existing_ldflags} -latomic".strip()
        _echo("Added -latomic to LDFLAGS (architecture requires libatomic)", console)

    # Some architectures need special compiler flags
    # xtensa: -mlongcalls is required to handle long function calls
    arch_cflags = {
        "xtensa": "-mlongcalls",
    }
    if arch in arch_cflags:
        extra_flags = arch_cflags[arch]
        existing_cflags = env.get("CFLAGS", "")
        existing_cxxflags = env.get("CXXFLAGS", "")
        env["CFLAGS"] = f"{existing_cflags} {extra_flags}".strip()
        env["CXXFLAGS"] = f"{existing_cxxflags} {extra_flags}".strip()
        _echo(
            f"Added {extra_flags} to CFLAGS/CXXFLAGS (architecture requirement)",
            console,
        )

    # Disable LTO to avoid issues with cross-compilation
    existing_cflags = env.get("CFLAGS", "")
    existing_cxxflags = env.get("CXXFLAGS", "")
    existing_ldflags = env.get("LDFLAGS", "")
    env["CFLAGS"] = f"{existing_cflags} -fno-lto".strip()
    env["CXXFLAGS"] = f"{existing_cxxflags} -fno-lto".strip()
    env["LDFLAGS"] = f"{existing_ldflags} -fno-lto".strip()
    _echo("Added -fno-lto to CFLAGS/CXXFLAGS/LDFLAGS (disable LTO)", console)

    _echo(f"Target triplet: {target}", console)
    _echo(f"Cross prefix:   {cross_compile}", console)
    if use_ccache:
        _echo("Using ccache:   yes", console)

    # Configure
    _echo(f"Configuring GDB for {arch}...", console)
    configure_cmd = [
        f"{config.gdb_src}/configure",
        f"--host={target}",
        f"--target={target}",
    ] + configure_opts.split()

    configure_log = build_dir / "configure.log"
    if not run_command(
        configure_cmd,
        cwd=build_dir,
        env=env,
        log_file=configure_log,
        verbose=verbose,
        console=console,
    ):
        _echo(f"FAILED: Configuration for {arch} failed", console, err=True)
        if not verbose:
            _echo(f"Check log: {configure_log}", console)
        return None

    _echo(f"SUCCESS: Build directory initialized for {arch}", console)
    _echo(f"Build directory: {build_dir}", console)

    return {"build_dir": build_dir, "env": env}


def build_single_gdb(
    arch: str,
    config: BuildConfig,
    parallel_jobs: int,
    clean: bool,
    configure_opts: str,
    verbose: bool = False,
    console: Optional[Console] = None,
) -> bool:
    """
    Build GDB for a single architecture.

    Args:
        arch: Architecture name
        config: Build configuration
        parallel_jobs: Number of parallel make jobs
        clean: Whether to clean before building
        configure_opts: Additional configure options
        verbose: Whether to show command output
        console: Rich console for coordinated output with progress bars

    Returns:
        True if build succeeded, False otherwise
    """
    _echo("=" * 50, console)
    _echo(f"Building GDB for: {arch}", console)
    _echo("=" * 50, console)

    build_dir = config.gdb_build_base / arch

    # Initialize if needed or if clean was requested
    if clean or not (build_dir / "Makefile").exists():
        init_result = init_single_gdb(
            arch, config, clean, configure_opts, verbose, console
        )
        if not init_result:
            return False
        build_dir = init_result["build_dir"]
        env = init_result["env"]
    else:
        _echo(f"Using existing configuration in {build_dir}", console)
        # Still need to setup the environment for the build
        toolchain_dir = config.output_base / arch
        cross_compile = find_cross_compile_prefix(toolchain_dir)
        if cross_compile is None:
            _echo(
                f"ERROR: Cannot find cross-compile prefix for {arch}", console, err=True
            )
            return False
        env = os.environ.copy()
        env["PATH"] = f"{toolchain_dir}/bin:{env.get('PATH', '')}"
        env["CROSS_COMPILE"] = cross_compile

        # Check for ccache and configure compiler commands
        use_ccache = check_ccache_available()
        if use_ccache:
            env["CC"] = f"ccache {cross_compile}-gcc"
            env["CXX"] = f"ccache {cross_compile}-g++"
            _echo("Using ccache:   yes", console)
        else:
            env["CC"] = f"{cross_compile}-gcc"
            env["CXX"] = f"{cross_compile}-g++"

        env["AR"] = f"{cross_compile}-ar"
        env["RANLIB"] = f"{cross_compile}-ranlib"

        # Some architectures need libatomic for atomic operations
        # Empirically determined via test-atomic.cpp/test-atomic.sh
        archs_needing_libatomic = ["microblaze"]
        if arch in archs_needing_libatomic:
            existing_ldflags = env.get("LDFLAGS", "")
            env["LDFLAGS"] = f"{existing_ldflags} -latomic".strip()

        # Some architectures need special compiler flags
        # xtensa: -mlongcalls is required to handle long function calls
        arch_cflags = {
            "xtensa": "-mlongcalls",
        }
        if arch in arch_cflags:
            extra_flags = arch_cflags[arch]
            existing_cflags = env.get("CFLAGS", "")
            existing_cxxflags = env.get("CXXFLAGS", "")
            env["CFLAGS"] = f"{existing_cflags} {extra_flags}".strip()
            env["CXXFLAGS"] = f"{existing_cxxflags} {extra_flags}".strip()

        # Disable LTO to avoid issues with cross-compilation
        existing_cflags = env.get("CFLAGS", "")
        existing_cxxflags = env.get("CXXFLAGS", "")
        existing_ldflags = env.get("LDFLAGS", "")
        env["CFLAGS"] = f"{existing_cflags} -fno-lto".strip()
        env["CXXFLAGS"] = f"{existing_cxxflags} -fno-lto".strip()
        env["LDFLAGS"] = f"{existing_ldflags} -fno-lto".strip()

    # Build
    _echo(f"Building GDB and gdbserver for {arch}...", console)
    build_log = build_dir / "build.log"
    success = run_command(
        ["make", f"-j{parallel_jobs}", "all-gdb", "all-gdbserver"],
        cwd=build_dir,
        env=env,
        log_file=build_log,
        verbose=verbose,
        console=console,
    )

    if success:
        _echo(f"SUCCESS: GDB for {arch} built successfully", console)

        # Show what was built
        _echo("Built executables:", console)
        for exe in ["gdb", "gdbserver"]:
            found = list(build_dir.rglob(exe))[:5]
            for f in found:
                _echo(f"  {f}", console)
    else:
        _echo(f"FAILED: Build for {arch} failed", console, err=True)
        if not verbose:
            _echo(f"Check log: {build_log}", console)

    return success


@click.group()
@click.pass_context
def cli(ctx):
    """Build tool for Buildroot toolchains and GDB."""
    ctx.ensure_object(dict)
    # Use the current working directory as the script directory
    script_dir = Path.cwd()
    ctx.obj["config"] = BuildConfig(script_dir)


@cli.command(name="build-toolchains")
@click.option(
    "-j",
    "--jobs",
    type=int,
    default=multiprocessing.cpu_count(),
    help="Number of parallel make jobs",
)
@click.option(
    "-c", "--clean", is_flag=True, help="Clean build directories before building"
)
@click.option(
    "-s",
    "--buildroot-src",
    type=click.Path(exists=True),
    help="Buildroot source directory",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Show command output instead of logging to files",
)
@click.option(
    "-k",
    "--keep-going",
    is_flag=True,
    help="Continue building remaining architectures after failures",
)
@click.argument("architectures", nargs=-1)
@click.pass_context
def build_toolchains(
    ctx, jobs, clean, buildroot_src, verbose, keep_going, architectures
):
    """
    Build Buildroot toolchains for one or more architectures.

    If no architectures are specified, builds all architectures
    defined in architectures.conf.

    Examples:

    \b
        # Build all toolchains
        build-tool build-toolchains

    \b
        # Build specific architectures
        build-tool build-toolchains aarch64 arm

    \b
        # Clean build with 8 jobs
        build-tool build-toolchains -c -j 8
    """
    config = ctx.obj["config"]

    if buildroot_src:
        config.buildroot_src = buildroot_src

    # Verify buildroot source exists
    if not Path(config.buildroot_src).exists():
        click.echo(
            f"ERROR: Buildroot source directory not found: {config.buildroot_src}",
            err=True,
        )
        click.echo("Set BUILDROOT_SRC environment variable or use -s option", err=True)
        sys.exit(1)

    # Load architectures
    all_archs = config.load_architectures()

    # Determine which architectures to build
    if architectures:
        archs_to_build = {}
        for arch in architectures:
            if arch not in all_archs:
                click.echo(f"ERROR: Unknown architecture '{arch}'", err=True)
                click.echo(f"Available: {', '.join(all_archs.keys())}", err=True)
                sys.exit(1)
            archs_to_build[arch] = all_archs[arch]
    else:
        archs_to_build = all_archs

    # Print summary
    click.echo("=" * 50)
    click.echo("Buildroot Toolchain Build")
    click.echo("=" * 50)
    click.echo(f"Buildroot source: {config.buildroot_src}")
    click.echo(f"Output directory: {config.output_base}")
    click.echo(f"Build directory:  {config.build_base}")
    click.echo(f"Parallel jobs:    {jobs}")
    click.echo(f"Architectures:    {len(archs_to_build)}")
    click.echo()

    # Create output directories
    config.output_base.mkdir(parents=True, exist_ok=True)
    config.build_base.mkdir(parents=True, exist_ok=True)

    # Build each architecture
    results = {}

    # Use progress bars when building multiple architectures
    use_progress = len(archs_to_build) > 1

    if use_progress:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
        ) as progress:
            overall_task = progress.add_task(
                "Building toolchains [0/{}]".format(len(archs_to_build)),
                total=len(archs_to_build),
            )
            current_task = progress.add_task("", total=None)

            for idx, (arch, defconfig) in enumerate(archs_to_build.items(), 1):
                total = len(archs_to_build)
                progress.update(
                    overall_task,
                    description=f"Building toolchains [{idx}/{total}] ({arch})",
                )
                progress.update(
                    current_task, description=f"Building {arch}...", completed=0
                )
                success = build_single_toolchain(
                    arch,
                    defconfig,
                    config,
                    jobs,
                    clean,
                    verbose,
                    progress.console,
                )
                results[arch] = "SUCCESS" if success else "FAILED"
                progress.update(overall_task, advance=1)

                # Stop on first failure unless keep-going is set
                if not success and not keep_going:
                    progress.stop()
                    click.echo(
                        "Stopping due to failure. "
                        "Use -k/--keep-going to continue with remaining architectures."
                    )
                    break
    else:
        for arch, defconfig in archs_to_build.items():
            success = build_single_toolchain(
                arch, defconfig, config, jobs, clean, verbose
            )
            results[arch] = "SUCCESS" if success else "FAILED"
            click.echo()

            # Stop on first failure unless keep-going is set
            if not success and not keep_going:
                click.echo(
                    "Stopping due to failure. "
                    "Use -k/--keep-going to continue with remaining architectures."
                )
                break

    # Write status file
    status_file = config.output_base / "build-status.txt"
    with open(status_file, "w") as f:
        for arch, status in results.items():
            f.write(f"{arch}: {status}\n")

    # Print summary
    click.echo("=" * 50)
    click.echo("Build Summary")
    click.echo("=" * 50)
    for arch, status in results.items():
        click.echo(f"{arch}: {status}")
    click.echo()
    click.echo(f"Toolchains are in: {config.output_base}")


@cli.command(name="build-gdb")
@click.option(
    "-j",
    "--jobs",
    type=int,
    default=multiprocessing.cpu_count(),
    help="Number of parallel make jobs",
)
@click.option(
    "-c", "--clean", is_flag=True, help="Clean build directories before building"
)
@click.option(
    "-s", "--gdb-src", type=click.Path(exists=True), help="GDB source directory"
)
@click.option(
    "-o",
    "--configure-opts",
    default="--enable-gdbserver --disable-sim",
    help="Additional configure options",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Show command output instead of logging to files",
)
@click.option(
    "-k",
    "--keep-going",
    is_flag=True,
    help="Continue building remaining architectures after failures",
)
@click.argument("architectures", nargs=-1)
@click.pass_context
def build_gdb(
    ctx, jobs, clean, gdb_src, configure_opts, verbose, keep_going, architectures
):
    """
    Build GDB/gdbserver for one or more architectures.

    Uses the toolchains built by build-toolchains. If no architectures
    are specified, builds for all available toolchains.

    Examples:

    \b
        # Build GDB for all toolchains
        build-tool build-gdb

    \b
        # Build for specific architectures
        build-tool build-gdb aarch64 arm

    \b
        # Clean build with custom configure options
        build-tool build-gdb -c -o "--enable-gdbserver --with-expat"
    """
    config = ctx.obj["config"]

    if gdb_src:
        config.gdb_src = gdb_src

    # Verify GDB source exists
    if not Path(config.gdb_src).exists():
        click.echo(f"ERROR: GDB source directory not found: {config.gdb_src}", err=True)
        click.echo("Set GDB_SRC environment variable or use -s option", err=True)
        sys.exit(1)

    # Determine which architectures to build
    if architectures:
        archs_to_build = list(architectures)
    else:
        archs_to_build = config.get_available_toolchains()
        if not archs_to_build:
            click.echo(f"ERROR: No toolchains found in {config.output_base}", err=True)
            click.echo("Run build-toolchains first", err=True)
            sys.exit(1)

    # Print summary
    click.echo("=" * 50)
    click.echo("GDB Build")
    click.echo("=" * 50)
    click.echo(f"GDB source:       {config.gdb_src}")
    click.echo(f"Toolchains base:  {config.output_base}")
    click.echo(f"Build directory:  {config.gdb_build_base}")
    click.echo(f"Parallel jobs:    {jobs}")
    click.echo(f"Configure opts:   {configure_opts}")
    click.echo(f"Architectures:    {len(archs_to_build)}")
    click.echo()

    # Create build directory
    config.gdb_build_base.mkdir(parents=True, exist_ok=True)

    # Build for each architecture
    results = {}

    # Use progress bars when building multiple architectures
    use_progress = len(archs_to_build) > 1

    if use_progress:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
        ) as progress:
            overall_task = progress.add_task(
                "Building GDB [0/{}]".format(len(archs_to_build)),
                total=len(archs_to_build),
            )
            current_task = progress.add_task("", total=None)

            for idx, arch in enumerate(archs_to_build, 1):
                progress.update(
                    overall_task,
                    description=f"Building GDB [{idx}/{len(archs_to_build)}] ({arch})",
                )
                progress.update(
                    current_task, description=f"Building GDB for {arch}...", completed=0
                )
                success = build_single_gdb(
                    arch,
                    config,
                    jobs,
                    clean,
                    configure_opts,
                    verbose,
                    progress.console,
                )
                results[arch] = "SUCCESS" if success else "FAILED"
                progress.update(overall_task, advance=1)

                # Stop on first failure unless keep-going is set
                if not success and not keep_going:
                    progress.stop()
                    click.echo(
                        "Stopping due to failure. "
                        "Use -k/--keep-going to continue with remaining architectures."
                    )
                    break
    else:
        for arch in archs_to_build:
            success = build_single_gdb(
                arch, config, jobs, clean, configure_opts, verbose
            )
            results[arch] = "SUCCESS" if success else "FAILED"
            click.echo()

            # Stop on first failure unless keep-going is set
            if not success and not keep_going:
                click.echo(
                    "Stopping due to failure. "
                    "Use -k/--keep-going to continue with remaining architectures."
                )
                break

    # Write status file
    status_file = config.gdb_build_base / "build-status.txt"
    with open(status_file, "w") as f:
        for arch, status in results.items():
            f.write(f"{arch}: {status}\n")

    # Print summary
    click.echo("=" * 50)
    click.echo("GDB Build Summary")
    click.echo("=" * 50)
    for arch, status in results.items():
        click.echo(f"{arch}: {status}")
    click.echo()
    click.echo(f"GDB builds are in: {config.gdb_build_base}")


@cli.command(name="init-toolchains")
@click.option(
    "-c", "--clean", is_flag=True, help="Clean build directories before initializing"
)
@click.option(
    "-s",
    "--buildroot-src",
    type=click.Path(exists=True),
    help="Buildroot source directory",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Show command output instead of logging to files",
)
@click.option(
    "-k",
    "--keep-going",
    is_flag=True,
    help="Continue initializing remaining architectures after failures",
)
@click.argument("architectures", nargs=-1)
@click.pass_context
def init_toolchains(ctx, clean, buildroot_src, verbose, keep_going, architectures):
    """
    Initialize Buildroot build directories (without building).

    This applies the defconfig and local configuration modifications,
    preparing the build directories for compilation.

    Examples:

    \b
        # Initialize all architectures
        build-tool init-toolchains

    \b
        # Initialize specific architectures
        build-tool init-toolchains aarch64 arm

    \b
        # Clean initialization
        build-tool init-toolchains -c aarch64
    """
    config = ctx.obj["config"]

    if buildroot_src:
        config.buildroot_src = buildroot_src

    # Verify buildroot source exists
    if not Path(config.buildroot_src).exists():
        click.echo(
            f"ERROR: Buildroot source directory not found: {config.buildroot_src}",
            err=True,
        )
        click.echo("Set BUILDROOT_SRC environment variable or use -s option", err=True)
        sys.exit(1)

    # Load architectures
    all_archs = config.load_architectures()

    # Determine which architectures to initialize
    if architectures:
        archs_to_init = {}
        for arch in architectures:
            if arch not in all_archs:
                click.echo(f"ERROR: Unknown architecture '{arch}'", err=True)
                click.echo(f"Available: {', '.join(all_archs.keys())}", err=True)
                sys.exit(1)
            archs_to_init[arch] = all_archs[arch]
    else:
        archs_to_init = all_archs

    # Print summary
    click.echo("=" * 50)
    click.echo("Buildroot Initialization")
    click.echo("=" * 50)
    click.echo(f"Buildroot source: {config.buildroot_src}")
    click.echo(f"Output directory: {config.output_base}")
    click.echo(f"Build directory:  {config.build_base}")
    click.echo(f"Architectures:    {len(archs_to_init)}")
    click.echo()

    # Create output directories
    config.output_base.mkdir(parents=True, exist_ok=True)
    config.build_base.mkdir(parents=True, exist_ok=True)

    # Initialize each architecture
    results = {}

    # Use progress bars when initializing multiple architectures
    use_progress = len(archs_to_init) > 1

    if use_progress:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
        ) as progress:
            overall_task = progress.add_task(
                "Initializing toolchains [0/{}]".format(len(archs_to_init)),
                total=len(archs_to_init),
            )
            current_task = progress.add_task("", total=None)

            for idx, (arch, defconfig) in enumerate(archs_to_init.items(), 1):
                total = len(archs_to_init)
                progress.update(
                    overall_task,
                    description=f"Initializing toolchains [{idx}/{total}] ({arch})",
                )
                progress.update(
                    current_task, description=f"Initializing {arch}...", completed=0
                )
                success = init_single_toolchain(
                    arch, defconfig, config, clean, verbose, progress.console
                )
                results[arch] = "SUCCESS" if success else "FAILED"
                progress.update(overall_task, advance=1)

                # Stop on first failure unless keep-going is set
                if not success and not keep_going:
                    progress.stop()
                    click.echo(
                        "Stopping due to failure. "
                        "Use -k/--keep-going to continue with remaining architectures."
                    )
                    break
    else:
        for arch, defconfig in archs_to_init.items():
            click.echo("=" * 50)
            click.echo(f"Initializing: {arch}")
            click.echo("=" * 50)
            success = init_single_toolchain(arch, defconfig, config, clean, verbose)
            results[arch] = "SUCCESS" if success else "FAILED"
            click.echo()

            # Stop on first failure unless keep-going is set
            if not success and not keep_going:
                click.echo(
                    "Stopping due to failure. "
                    "Use -k/--keep-going to continue with remaining architectures."
                )
                break

    # Print summary
    click.echo("=" * 50)
    click.echo("Initialization Summary")
    click.echo("=" * 50)
    for arch, status in results.items():
        click.echo(f"{arch}: {status}")
    click.echo()


@cli.command(name="init-gdb")
@click.option(
    "-c", "--clean", is_flag=True, help="Clean build directories before initializing"
)
@click.option(
    "-s", "--gdb-src", type=click.Path(exists=True), help="GDB source directory"
)
@click.option(
    "-o",
    "--configure-opts",
    default="--enable-gdbserver --disable-sim",
    help="Additional configure options",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Show command output instead of logging to files",
)
@click.option(
    "-k",
    "--keep-going",
    is_flag=True,
    help="Continue initializing remaining architectures after failures",
)
@click.argument("architectures", nargs=-1)
@click.pass_context
def init_gdb(ctx, clean, gdb_src, configure_opts, verbose, keep_going, architectures):
    """
    Initialize GDB build directories (without building).

    This runs the configure command, preparing the build directories
    for compilation.

    Examples:

    \b
        # Initialize GDB for all toolchains
        build-tool init-gdb

    \b
        # Initialize for specific architectures
        build-tool init-gdb aarch64 arm

    \b
        # Clean initialization with custom configure options
        build-tool init-gdb -c -o "--enable-gdbserver --with-expat"
    """
    config = ctx.obj["config"]

    if gdb_src:
        config.gdb_src = gdb_src

    # Verify GDB source exists
    if not Path(config.gdb_src).exists():
        click.echo(f"ERROR: GDB source directory not found: {config.gdb_src}", err=True)
        click.echo("Set GDB_SRC environment variable or use -s option", err=True)
        sys.exit(1)

    # Determine which architectures to initialize
    if architectures:
        archs_to_init = list(architectures)
    else:
        archs_to_init = config.get_available_toolchains()
        if not archs_to_init:
            click.echo(f"ERROR: No toolchains found in {config.output_base}", err=True)
            click.echo("Run build-toolchains or init-toolchains first", err=True)
            sys.exit(1)

    # Print summary
    click.echo("=" * 50)
    click.echo("GDB Initialization")
    click.echo("=" * 50)
    click.echo(f"GDB source:       {config.gdb_src}")
    click.echo(f"Toolchains base:  {config.output_base}")
    click.echo(f"Build directory:  {config.gdb_build_base}")
    click.echo(f"Configure opts:   {configure_opts}")
    click.echo(f"Architectures:    {len(archs_to_init)}")
    click.echo()

    # Create build directory
    config.gdb_build_base.mkdir(parents=True, exist_ok=True)

    # Initialize for each architecture
    results = {}

    # Use progress bars when initializing multiple architectures
    use_progress = len(archs_to_init) > 1

    if use_progress:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
        ) as progress:
            overall_task = progress.add_task(
                "Initializing GDB [0/{}]".format(len(archs_to_init)),
                total=len(archs_to_init),
            )
            current_task = progress.add_task("", total=None)

            for idx, arch in enumerate(archs_to_init, 1):
                total = len(archs_to_init)
                progress.update(
                    overall_task,
                    description=f"Initializing GDB [{idx}/{total}] ({arch})",
                )
                progress.update(
                    current_task,
                    description=f"Initializing GDB for {arch}...",
                    completed=0,
                )
                init_result = init_single_gdb(
                    arch, config, clean, configure_opts, verbose, progress.console
                )
                results[arch] = "SUCCESS" if init_result else "FAILED"
                progress.update(overall_task, advance=1)

                # Stop on first failure unless keep-going is set
                if not init_result and not keep_going:
                    progress.stop()
                    click.echo(
                        "Stopping due to failure. "
                        "Use -k/--keep-going to continue with remaining architectures."
                    )
                    break
    else:
        for arch in archs_to_init:
            click.echo("=" * 50)
            click.echo(f"Initializing GDB for: {arch}")
            click.echo("=" * 50)
            init_result = init_single_gdb(arch, config, clean, configure_opts, verbose)
            results[arch] = "SUCCESS" if init_result else "FAILED"
            click.echo()

            # Stop on first failure unless keep-going is set
            if not init_result and not keep_going:
                click.echo(
                    "Stopping due to failure. "
                    "Use -k/--keep-going to continue with remaining architectures."
                )
                break

    # Print summary
    click.echo("=" * 50)
    click.echo("Initialization Summary")
    click.echo("=" * 50)
    for arch, status in results.items():
        click.echo(f"{arch}: {status}")
    click.echo()


@cli.command(name="shell")
@click.argument("architectures", nargs=-1)
@click.pass_context
def shell(ctx, architectures):
    """
    Spawn a subshell with toolchain(s) in PATH.

    Opens an interactive shell with the PATH variable adjusted to include
    the toolchain bin directories. This allows you to use cross-compilation
    tools directly.

    If no architectures are specified, all available toolchains are added
    to PATH.

    Examples:

    \b
        # Shell with all toolchains in PATH
        build-tool shell

    \b
        # Shell with specific toolchains
        build-tool shell aarch64 arm

    \b
        # Use the toolchain
        build-tool shell aarch64
        $ aarch64-buildroot-linux-gnu-gcc --version
    """
    config = ctx.obj["config"]

    # Determine which architectures to include
    if architectures:
        archs_to_use = list(architectures)
    else:
        archs_to_use = config.get_available_toolchains()
        if not archs_to_use:
            click.echo(f"ERROR: No toolchains found in {config.output_base}", err=True)
            click.echo("Run build-toolchains first", err=True)
            sys.exit(1)

    # Build PATH with all requested toolchain bin directories
    toolchain_paths = []
    cross_compiles = []

    for arch in archs_to_use:
        toolchain_dir = config.output_base / arch
        if not toolchain_dir.exists():
            click.echo(
                f"WARNING: Toolchain not found for {arch}: {toolchain_dir}", err=True
            )
            continue

        bin_dir = toolchain_dir / "bin"
        if bin_dir.exists():
            toolchain_paths.append(str(bin_dir))

            # Get cross-compile prefix for this arch
            cross_compile = find_cross_compile_prefix(toolchain_dir)
            if cross_compile:
                cross_compiles.append(f"{arch}={cross_compile}")

    if not toolchain_paths:
        click.echo("ERROR: No valid toolchains found", err=True)
        sys.exit(1)

    # Setup environment
    env = os.environ.copy()
    env["PATH"] = ":".join(toolchain_paths + [env["PATH"]])

    # Set helpful environment variables
    env["BUILD_TOOL_ARCHS"] = " ".join(archs_to_use)

    # If only one architecture, set CROSS_COMPILE
    if len(archs_to_use) == 1 and cross_compiles:
        arch_prefix = cross_compiles[0].split("=", 1)[1]
        env["CROSS_COMPILE"] = arch_prefix
        env["CC"] = f"{arch_prefix}-gcc"
        env["CXX"] = f"{arch_prefix}-g++"
        env["AR"] = f"{arch_prefix}-ar"
        env["RANLIB"] = f"{arch_prefix}-ranlib"

    # Print info
    click.echo("=" * 70)
    click.echo("Build Tool Shell")
    click.echo("=" * 70)
    click.echo(f"Architectures: {', '.join(archs_to_use)}")
    click.echo()
    click.echo("Toolchain bin directories added to PATH:")
    for path in toolchain_paths:
        click.echo(f"  {path}")

    if cross_compiles:
        click.echo()
        click.echo("Cross-compile prefixes:")
        for cc in cross_compiles:
            click.echo(f"  {cc}")

    if len(archs_to_use) == 1:
        click.echo()
        click.echo("Environment variables set:")
        click.echo(f"  CROSS_COMPILE={env.get('CROSS_COMPILE', '')}")
        click.echo(f"  CC={env.get('CC', '')}")
        click.echo(f"  CXX={env.get('CXX', '')}")

    click.echo()
    click.echo("Type 'exit' to leave this shell")
    click.echo("=" * 70)
    click.echo()

    # Spawn interactive shell
    shell_cmd = env.get("SHELL", "/bin/bash")
    try:
        subprocess.run([shell_cmd], env=env)
    except KeyboardInterrupt:
        pass

    click.echo()
    click.echo("Exited build tool shell")


@cli.command(name="list-archs")
@click.pass_context
def list_archs(ctx):
    """List available architectures and their build status."""
    config = ctx.obj["config"]

    all_archs = config.load_architectures()
    toolchains = set(config.get_available_toolchains())

    click.echo("Available Architectures:")
    click.echo()
    click.echo(f"{'Architecture':<15} {'Defconfig':<40} {'Toolchain':<10}")
    click.echo("-" * 70)

    for arch, defconfig in sorted(all_archs.items()):
        has_toolchain = "Built" if arch in toolchains else "Not built"
        click.echo(f"{arch:<15} {defconfig:<40} {has_toolchain:<10}")


def main():
    """Entry point for the CLI."""
    cli()


if __name__ == "__main__":
    main()
