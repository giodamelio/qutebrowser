{ pkgs, lib, config, inputs, ... }:

let
  # nixpkgs ships pytest-bdd 7.1.2 and no gherkin-official, but the repo pins
  # pytest-bdd 8.1.0 (misc/requirements/requirements-tests.txt), and 7.1.2
  # fails collection under pytest 9 with filterwarnings = error.
  pythonBase = pkgs.python312.override {
    self = pythonBase;
    packageOverrides = pyfinal: pyprev: {
      gherkin-official = pyfinal.buildPythonPackage rec {
        pname = "gherkin_official";
        version = "29.0.0";
        pyproject = true;
        src = pkgs.fetchPypi {
          inherit pname version;
          hash = "sha256-2+oyVhFY8CKA11edF5sBkWDQcs4IMZdiXi+Apndrues=";
        };
        build-system = [ pyfinal.setuptools ];
        dependencies = [ pyfinal.typing-extensions ];
        pythonImportsCheck = [ "gherkin" ];
      };
      pytest-bdd = pyprev.pytest-bdd.overridePythonAttrs (old: rec {
        version = "8.1.0";
        src = pkgs.fetchFromGitHub {
          owner = "pytest-dev";
          repo = "pytest-bdd";
          tag = version;
          hash = "sha256-jxrjUXmyDEfw1sxwnlSUAfz3Kkv/4TwKFx7cone0Eyw=";
        };
        patches = [ ];
        dependencies = (old.dependencies or old.propagatedBuildInputs or [ ]) ++ [
          pyfinal.gherkin-official
          pyfinal.packaging
        ];
        doCheck = false;
      });
    };
  };

  python = pythonBase.withPackages (ps: with ps; [
    # Core runtime dependencies
    pyqt6-webengine
    pyqt6
    jinja2
    pyyaml
    pygments
    adblock
    colorama

    # Test dependencies
    pytest
    pytest-bdd
    pytest-benchmark
    pytest-cov
    pytest-instafail
    pytest-mock
    pytest-qt
    pytest-rerunfailures
    pytest-repeat
    pytest-xdist
    pytest-xvfb
    hypothesis
    beautifulsoup4
    cheroot
    flask
    coverage
    pyvirtualdisplay
    pillow
    tldextract

    # Dev/lint dependencies
    tox
    flake8
    pylint
    mypy
    vulture
    pympler

    # Build tools
    setuptools
    build
    pip
  ]);
in
{
  packages = with pkgs; [
    python

    # Qt6 and related
    qt6.qtbase
    qt6.qtwebengine

    # Documentation generation
    asciidoc
    docbook_xml_dtd_45
    docbook-xsl-nons
    libxml2
    libxslt

    # For running tests with virtual display
    xvfb-run
    xorg.xorgserver
  ];

  env = {
    # Ensure Qt can find its plugins
    QT_PLUGIN_PATH = "${pkgs.qt6.qtbase}/${pkgs.qt6.qtbase.qtPluginPrefix}";
    QT_QPA_PLATFORM_PLUGIN_PATH = "${pkgs.qt6.qtbase}/${pkgs.qt6.qtbase.qtPluginPrefix}/platforms";
  };

  # The same override as giodamelio/giopkgs' qutebrowser package, but built
  # from this checkout. Build it with `devenv build outputs.qutebrowser`.
  outputs.qutebrowser = pkgs.qutebrowser.overrideAttrs (oldAttrs: {
    version = "${lib.head (builtins.match ''.*__version__ = "([^"]+)".*''
      (builtins.readFile ./qutebrowser/__init__.py))}-dev";

    src = lib.fileset.toSource {
      root = ./.;
      fileset = lib.fileset.difference ./. (lib.fileset.unions (map lib.fileset.maybeMissing [
        ./.benchmarks
        ./.claude
        ./.devenv
        ./.direnv
        ./.git
        ./.hypothesis
        ./.jj
        ./.mypy_cache
        ./.pytest_cache
        ./.superpowers
        ./.workspaces
        ./NEW-SESSIONS-DESIGN.md
        ./docs
        ./relative
        ./tmp
      ]));
    };

    meta = oldAttrs.meta // {
      description = "qutebrowser from giodamelio's fork, built from this checkout";
    };
  });

  # Built at run time rather than referenced from here, so entering the shell
  # doesn't rebuild the browser after every source change.
  tasks = let
    run = args: ''
      cd "$DEVENV_ROOT"
      out=$(devenv build outputs.qutebrowser | ${pkgs.jq}/bin/jq -r '."outputs.qutebrowser"')
      exec "$out/bin/qutebrowser" ${args}
    '';
  in {
    "qutebrowser:run".exec = run "";
    "qutebrowser:run-temp".exec = run "--temp-basedir";
  };

  enterShell = ''
    echo "qutebrowser development environment"
    echo "Run: python -m qutebrowser --debug --temp-basedir"
    echo "Tests: pytest tests/"
    echo "Lint: tox -e flake8,pylint,mypy-pyqt6"
  '';
}
