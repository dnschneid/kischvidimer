<!--
SPDX-FileCopyrightText: (C) 2025 Rivos Inc.
SPDX-License-Identifier: Apache-2.0
-->

# kischvidimer

The KiCad schematic viewer / differ / merger.

(although merging is not implemented yet)

Supports KiCad 7 through KiCad 10.

## tl;dr

kischvidimer generates a self-contained HTML webapp with your KiCad schematic
embedded. The app includes metadata and is simultaneously smaller/faster and
more feature-rich than PDFs. You can view the file locally, send it as an email
attachment, or host it on a webserver.

Give it a test whirl:

```
python3 -m kischvidimer schgen .../your_project.kicad_pro -o your_project.html
```

Or install it:

```
pip3 install git+https://gitlab.com/dnschneid/kischvidimer.git
kischvidimer schgen your_project.kicad_pro -o your_project.html
```

Live demos are available at https://dnschneid.gitlab.io/kischvidimer

## Commands

### `schgen` -- Generate a schematic viewer

The main command. Renders a KiCad project into a self-contained HTML file.

```
kischvidimer schgen [options] [git_rev] project.kicad_pro
```

**Examples:**

```sh
# Render a schematic and open it in a browser
kischvidimer schgen your_project.kicad_pro

# Write to a file instead
kischvidimer schgen your_project.kicad_pro -o your_project.html

# Render a specific git revision
kischvidimer schgen HEAD~1 your_project.kicad_pro -o old.html

# Diff against a git revision (shows changes between revision and working tree)
kischvidimer schgen HEAD.. your_project.kicad_pro

# Diff between two git revisions
kischvidimer schgen HEAD~3..HEAD your_project.kicad_pro

# Diff, only showing pages with changes
kischvidimer schgen HEAD.. your_project.kicad_pro -d

# Override the page border worksheet
kischvidimer schgen -w custom_border.kicad_wks your_project.kicad_pro

# Scrub sensitive data before rendering (regex)
kischvidimer schgen -s 'CONFIDENTIAL|SECRET' your_project.kicad_pro

# View usage help
kischvidimer schgen -h
```

### `diff` -- Semantic diff of KiCad files

Compares two KiCad files and outputs differences in text or SVG format.
Great for CI integration!

```sh
# Show differences between two schematic files
kischvidimer diff base.kicad_sch target.kicad_sch

# Output an SVG of the diff
kischvidimer diff --svg base.kicad_sch target.kicad_sch
# SVG output includes the textual diff at the end as a comment.

# Quiet mode (exit code only)
kischvidimer diff -q base.kicad_sch target.kicad_sch

# Diff a subset of the file
kischvidimer diff --selector '/symbol_instances' base.kicad_sch target.kicad_sch
```

### `diffui` -- Interactive diff UI

Launches a browser-based UI for viewing or comparing individual schematic files.
Unlike `schgen`, this operates on `.kicad_sch` files directly rather than
`.kicad_pro` projects.

```sh
# View a schematic page interactively
kischvidimer diffui page.kicad_sch

# Diff two schematics interactively
kischvidimer diffui base.kicad_sch target.kicad_sch

# Force mode with explicit flag
kischvidimer diffui -2 base.kicad_sch target.kicad_sch
```

### Other commands

The following subcommands are primarily used for debugging and development:

| Command      | Description                                  |
|--------------|----------------------------------------------|
| `kicad_pro`  | Parse and dump a `.kicad_pro` project file   |
| `kicad_sch`  | Parse and dump a `.kicad_sch` schematic file |
| `kicad_sym`  | Parse and dump a `.kicad_sym` symbol library |
| `kicad_wks`  | Parse and dump a `.kicad_wks` worksheet file |
| `sexp`       | Parse and dump an S-expression file          |
| `bmp`        | Convert BMP to PNG                           |
| `jpg`, `png` | Read JPEG and PNG metadata (size in mm)      |
| `git`        | Print the git version description of a path  |

## Viewer features

The generated HTML viewer includes:

- **Pan and zoom** -- mouse wheel, pinch-to-zoom on touch devices, or keyboard
  arrows / `+` / `-`
- **Page navigation** -- page list sidebar, PageUp/PageDown keys, or scroll past
  the edge of a page
- **Search** -- Ctrl+F or F3 to search components, nets, pins, and text; Enter
  to cycle through results, Ctrl+Enter to jump to the nearest result on or after
  the current page, Shift+(Ctrl+)+Enter to go in reverse
- **Tooltips** -- hover over components and nets for metadata
- **Hyperlinks** -- double-click sheet instances to jump to connected pages,
  double-click symbols to launch datasheets, and all URLs are clickable
- **Deep linking** -- if you host the generated HTML file somewhere, you can
  create deep links to components, nets, pages, etc
- **Print (to PDF)** -- Ctrl+P gives you a normal PDF of the complete design
- **Diff view** -- when rendered with a git revision, shows changes with an
  interactive animation between base and target, with a diff list sidebar
- **Feedback URL** -- provide a button to click for schematic review feedback
- **Themes** -- light AND dark!

## CI integration

### GitLab CI

Add the following to your `.gitlab-ci.yml` to automatically generate schematic
HTML files whenever `.kicad_pro` or `.kicad_sch` files change:

```yaml
include: "https://gitlab.com/dnschneid/kischvidimer/raw/main/ci/gitlab.yml"
```

This provides a `schgen` job that finds all `.kicad_pro` files in your repo and
generates corresponding `.html` artifacts.

### GitHub Actions

Use the `schgen` composite action from the GitHub mirror:

```yaml
jobs:
  schematic:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dnschneid/kischvidimer/ci/schgen@main
        with:
          kicad_pro: path/to/your_project.kicad_pro
          output: your_project.html
      - uses: actions/upload-artifact@v4
        with:
          name: schematic
          path: your_project.html
```

**Action inputs:**

| Input       | Required | Default       | Description                       |
|-------------|----------|---------------|-----------------------------------|
| `kicad_pro` | yes      | --            | Path to the `.kicad_pro` file     |
| `output`    | no       | `output.html` | Output file or directory          |
| `args`      | no       | `""`          | Additional arguments for `schgen` |

To generate all schematics in a repo:

```yaml
  - uses: actions/checkout@v4
  - name: Install kischvidimer
    run: pip install git+https://github.com/dnschneid/kischvidimer.git fonttools
  - name: Generate schematics
    run: find -name '*.kicad_pro' -exec kischvidimer schgen {} -o {}.html \;
```

## Current limitations

- **No merging yet** -- 3-way merge is not yet implemented.
- **Stable KiCad 7+ only** -- No support for KiCad 6 s-exps or old .sch files.
  Support for the latest features in development tends to lag.
- **Not all KiCad features are implemented** -- KiCad has a *lot* of features;
  some are major and some are minor but difficult/inaccurate to implement.
  kischvidimer tries to render as much as possible but will never reach
  feature-, bug-, or pixel-parity. If your design is broken, file a bug report
  to help prioritize what's missing.

### Known missing schematic features

- Component classes
- Custom fonts
- Embedded files
- Flat schematics
- Netclasses
- Rendering configuration from Schematic Setup
- Several types of variable references
- String-number concatentation with plus (+) in text expressions
- The inch unit symbol (") in text expressions
- Variants

## What about layout?
Nope. Try [KiCanvas](https://kicanvas.org/) instead?

## OK then why does this exist?

Think of it as a PDF export of your schematic, but way, way better. kischvidimer
is designed to scale well to handle absolutely ginormous schematics, complex
variable usage, and large amounts of hierarchy and reuse.

As an example, exporting a real-life 158-page schematic as a PDF results in a
42MiB file. The HTML file generated by kischvidimer is 1.8MiB (<5%!), includes a
bunch of metadata and navigational aids, and can be viewed with ease on your
phone. You can even hit the print button and generate a massive PDF if you so
desire.

The HTML file has zero external dependencies and will not attempt to communicate
with any server (unless you launch a datasheet, of course), so you really can
treat it just like a PDF.

## KiCad already has SVG output!

It does! And the output is visually perfect! But the files are large, have hacks
of their own around text glyphs, and are difficult to inject metadata into. It
would be nigh impossible to implement the diffing and merging UI with them.

## How to pronounce kischvidimer?

Well, KiCad is pronounced KEY-cad, so I guess it should be KEY-schvidimer.
