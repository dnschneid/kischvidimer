# SPDX-FileCopyrightText: (C) 2025 Rivos Inc.
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
# SPDX-License-Identifier: Apache-2.0

import importlib
import os
import sys


def main(argv=sys.argv):
  p = os.path.dirname(__file__)
  mods = [
    m[:-3] for m in os.listdir(p) if m.endswith(".py") and not m.startswith("_")
  ]
  if len(argv) <= 1 or argv[1] not in mods:
    print(f"USAGE: {argv[0]} COMMAND ...", file=sys.stderr)
    print(f"Recognized commands:", file=sys.stderr)
    for mod in sorted(mods):
      if "def main(" in open(os.path.join(p, f"{mod}.py"), "r").read():
        print(f"  {mod}", file=sys.stderr)
    return 2
  mod = f".{argv[1]}"
  args = [f"{argv[0]} {argv[1]}"] + argv[2:]
  return importlib.import_module(f".{argv[1]}", __package__).main(args)


if __name__ == "__main__":
  sys.exit(main(sys.argv))
