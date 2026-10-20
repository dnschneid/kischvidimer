# SPDX-FileCopyrightText: (C) 2026 RosaicLabs
# SPDX-License-Identifier: Apache-2.0
{
  description = "KiCad schematic HTML viewer generator";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      inherit (nixpkgs) lib;
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems = f: lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
      version = "0+g${self.shortRev or self.dirtyShortRev or "dirty"}";
      package = ps: ps.callPackage ./nix/package.nix { inherit version; };
    in
    {
      # python3.withPackages (ps: [ ps.kischvidimer ]) with any Python.
      overlays.default = final: prev: {
        pythonPackagesExtensions = prev.pythonPackagesExtensions ++ [
          (pyfinal: pyprev: { kischvidimer = package pyfinal; })
        ];
      };

      packages = forAllSystems (
        pkgs:
        let
          python = package pkgs.python3Packages;
        in
        {
          default = pkgs.python3Packages.toPythonApplication python;
          inherit python;
        }
      );

      checks = forAllSystems (pkgs: {
        inherit (self.packages.${pkgs.stdenv.hostPlatform.system}) default python;
      });

      formatter = forAllSystems (pkgs: pkgs.nixfmt);
    };
}
