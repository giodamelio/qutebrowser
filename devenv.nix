{ pkgs, lib, config, inputs, ... }:

let
  python = pkgs.python312.withPackages (ps: with ps; [
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
  ];

  env = {
    # Ensure Qt can find its plugins
    QT_PLUGIN_PATH = "${pkgs.qt6.qtbase}/${pkgs.qt6.qtbase.qtPluginPrefix}";
    QT_QPA_PLATFORM_PLUGIN_PATH = "${pkgs.qt6.qtbase}/${pkgs.qt6.qtbase.qtPluginPrefix}/platforms";
  };

  enterShell = ''
    echo "qutebrowser development environment"
    echo "Run: python -m qutebrowser --debug --temp-basedir"
    echo "Tests: pytest tests/"
    echo "Lint: tox -e flake8,pylint,mypy-pyqt6"
  '';
}
