# cake-taste-64

Runs bounded N64 experiments and records native-output evidence.

> This is a personal tool. It is not officially maintained and may change or break without notice.

## Install

Python 3.13 or newer, in its own virtual environment.

```sh
python3.13 -m venv ~/.venvs/taste && . ~/.venvs/taste/bin/activate
pip install git+https://github.com/AbuN64/cache-64 git+https://github.com/AbuN64/cake-taste-64
```

The recompiled oracle also needs the MIPS binutils and N64Recomp at its pin, built beside a cake-taste-64 checkout:

```sh
sudo apt install binutils-mips-linux-gnu libsdl2-dev
git clone https://github.com/N64Recomp/N64Recomp && cd N64Recomp
git checkout "$(awk '$1=="N64Recomp"{print $2}' ../cake-taste-64/hosts/n64recomp/PINS)" && git submodule update --init --recursive
git apply ../cake-taste-64/hosts/n64recomp/patches/n64recomp-jtbl-kuseg-alias-and-bounds.diff
cmake -B build -DCMAKE_BUILD_TYPE=RelWithDebInfo && cmake --build build
```

Point the profile's `recomp.tools` at `build/N64Recomp` and `build/RSPRecomp`.

The host (`hosts/n64recomp`) also builds against N64ModernRuntime and RT64 at their pins, each with its patches, and
runs headless (SDL's offscreen video driver: no window, RT64 on a Vulkan headless surface; Mesa RADV or lavapipe):

```sh
sudo apt install cmake ninja-build gcc-14 g++-14 mesa-vulkan-drivers ffmpeg
cake-taste-64/hosts/n64recomp/deps.sh ~/src/oracle-deps   # pinned checkouts with every patch applied
```

Configure with the `N64R_*` inputs in `hosts/n64recomp/CMakeLists.txt` (`-DN64R_RUNTIME_SRC=~/src/oracle-deps/N64ModernRuntime
-DN64R_RT64_SRC=~/src/oracle-deps/rt64`), then point `repro.capture` (`host`, `host_build`, `env` with
`SDL_VIDEODRIVER=offscreen`, `N64R_ROM`) at it. No display server is needed; an X11 `DISPLAY` is refused.

## Usage

```sh
taste --profile project.json profile
taste --help
```

Profiles, commands and the stream are in [docs/usage.md](docs/usage.md).

## Development

```sh
sh ci/check   # prepares the private .venv from current source revisions, then runs lint and tests
```

## License

Released under [GNU GPL version 3 or later](LICENSE).

Oracle entry capture accepts `taste repro capture --condition EXPR`. The GDB expression must be true at the entry; `--hit` counts only matching entries. Use explicit host symbols or entry registers to select the replay poll and arguments. The condition is recorded and separates both successful captures and cached misses. An invalid expression fails capture.

## One owner per cache

AbuCache64 owns every cache, content store, worker slot, pool, executor and timing path (abucache.compile, store,
slots, pool, stages). `ci/abucache-audit`, run by the check entry point, refuses a new one here naming that owner; the
files it still lists move to abucache and leave the list, nothing joins it.

Stage duration results use `abucache.stages.timed()` and its `Timing.seconds` observation (seconds, including running work).
This requires AbuCache64 with the duration observation API introduced at `43bb0be`; first-party dependencies resolve
their landed main revision rather than retaining a consumer commit pin. Deadline and heartbeat clocks retain their scheduling role.

The CI environment is private to the checkout. `ci/ensure_venv.py` resolves the source names declared in
`tool.bootstrap.sources` to their landed revisions before installing dependencies. Its reuse key includes
`pyproject.toml` and every resolved requirement; a new AbuCache64 revision forces a fresh environment.
Bootstrap uses the standard library and does not import or borrow the installed toolkit.

`tests/test_declared_dependencies.py` fails when src or ci import a third-party module `pyproject.toml`
does not declare.

## The oracle

`ci/oracle build` builds the N64Recomp oracle (`n64host`) and runs its self-check (boot-idle twice, the two state
hashes must be equal); `ci/oracle selfcheck [HOST]` repeats the check. It is heavy: run it on host W through
`mo-terminal Admission Invoke --Class Batch`. Every path comes from the repository, none from the environment:

- the decomp is the sibling working copy `../RageWarsDecomp` (its `roms/` holds the dumps, its history the PINS commit);
- the dependencies (N64Recomp, N64ModernRuntime, rt64, AbuMas) are fetched at their PINS into the git-ignored
  `.state/oracle-deps/`; `abumas` is built from the AbuMas source there into `.state/tools/abumas`;
- the build is `.state/oracle/<sha>/`, `.state/oracle/live` links the self-checked one; the project profile the recompile
  derives from is the tracked `hosts/n64recomp/ragewars.profile.json` (workspace-relative paths, resolved against the
  file), `.state/oracle-facts.json` is derived from it and `.state/cfg/` is the host config directory;
- `unbake` comes from the private venv (`oracle` extra of `pyproject.toml`), never from PATH, and runs with
  `--host .state/unbake.toml`, the host config `hosts/n64recomp/host_config.py` writes from unbake's own template (tools
  from PATH, `abumas` from `.state/tools`, cache and toolchains inside `.state/`); the operator's
  `~/.config/unbake/unbake.toml` is never read.
