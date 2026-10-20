# SPDX-FileCopyrightText: (C) 2026 RosaicLabs
# SPDX-License-Identifier: Apache-2.0

# kischvidimer for any Python package set: callPackage it from one, as
# flake.nix's overlay does.
{
  lib,
  buildPythonPackage,
  setuptools,
  setuptools-scm,
  fonttools,
  git,
  version ? "0",
}:
buildPythonPackage {
  pname = "kischvidimer";
  inherit version;
  pyproject = true;

  src = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
      ../kischvidimer
      ../LICENSES
      ../pyproject.toml
      ../README.md
    ];
  };

  build-system = [
    setuptools
    setuptools-scm
  ];
  dependencies = [ fonttools ];

  env.SETUPTOOLS_SCM_PRETEND_VERSION = version;

  # schgen and diff read other revisions with git.
  makeWrapperArgs = [
    "--suffix"
    "PATH"
    ":"
    (lib.makeBinPath [ git ])
  ];

  pythonImportsCheck = [ "kischvidimer" ];

  meta = {
    description = "KiCad schematic HTML viewer generator";
    homepage = "https://gitlab.com/dnschneid/kischvidimer";
    license = lib.licenses.asl20;
    mainProgram = "kischvidimer";
  };
}
