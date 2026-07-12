# nix/devShell.nix — Dev shell that delegates setup to each package
#
# Each npm workspace package exposes passthru.packageJsonPath (e.g.
# "ui-tui/package.json").  This file collects them all and passes the
# list to mkNpmDevShellHook, which stamps all package.jsons at once,
# then runs a single `npm i --package-lock-only` if any changed and
# `npm ci` if the lockfile changed.
{ ... }:
{
  perSystem =
    { pkgs, self', ... }:
    let
      packages = builtins.attrValues self'.packages;
      herculesNpmLib = self'.packages.default.passthru.herculesNpmLib;

      # Collect all packageJsonPath values from npm workspace packages.
      npmPackageJsonPaths = builtins.filter (p: p != null) (
        map (p: p.passthru.packageJsonPath or null) packages
      );

      # Non-npm packages may have their own devShellHook (e.g. hercules-agent
      # stamps pyproject.toml + uv.lock for Python venv setup).
      nonNpmHooks = map (p: p.passthru.devShellHook or "") packages;
      combinedNonNpm = pkgs.lib.concatStringsSep "\n" (builtins.filter (h: h != "") nonNpmHooks);
    in
    {
      devShells.default = pkgs.mkShell {
        packages =
          with pkgs;
          [
            (pkgs.runCommand "hercules" { } ''
              mkdir -p $out/bin
              install -Dm755 ${../hercules} $out/bin/hercules
            '')
            (pkgs.runCommand "dev-sandbox" { } ''
              mkdir -p $out/bin
              install -Dm755 ${../scripts/dev-sandbox.sh} $out/bin/sandbox
            '')
            uv
          ]
          ++ self'.packages.default.passthru.devDeps;
        shellHook = ''
          ${combinedNonNpm}
          ${herculesNpmLib.mkNpmDevShellHook npmPackageJsonPaths}

          # for the devshell to pick up the src
          export HERCULES_PYTHON_SRC_ROOT=$(git rev-parse --show-toplevel)
          echo "Hercules Agent dev shell in $HERCULES_PYTHON_SRC_ROOT"
          echo "Ready. Run 'hercules' or 'sandbox hercules' to start."
        '';
      };
    };
}
