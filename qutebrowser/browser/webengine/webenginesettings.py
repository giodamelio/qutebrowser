# SPDX-FileCopyrightText: Freya Bruhin (The Compiler) <mail@qutebrowser.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Bridge from QWebEngineSettings to our own settings.

Module attributes:
    ATTRIBUTES: A mapping from internal setting names to QWebEngineSetting enum
                constants.
"""

import os
import operator
import pathlib
import dataclasses
from typing import cast, Any, TYPE_CHECKING

from qutebrowser.qt import machinery
from qutebrowser.qt.core import QUrl
from qutebrowser.qt.gui import QFont
from qutebrowser.qt.widgets import QApplication
from qutebrowser.qt.webenginecore import (QWebEngineSettings, QWebEngineProfile,
                                          QWebEngineScript)

from qutebrowser.browser import history, shared, greasemonkey
from qutebrowser.browser.webengine import (spell, webenginequtescheme, cookies,
                                           webenginedownloads, notification,
                                           profiles)
from qutebrowser.config import config, websettings
from qutebrowser.config.websettings import AttributeInfo as Attr
from qutebrowser.misc import containers, pakjoy
from qutebrowser.utils import (standarddir, qtutils, message, log,
                               urlmatch, usertypes, objreg, version, utils,
                               javascript, resources)
if TYPE_CHECKING:
    from qutebrowser.browser.webengine import interceptor

# Mapping worlds from usertypes.JsWorld to QWebEngineScript world IDs.
JS_WORLD_MAP = {
    usertypes.JsWorld.main: QWebEngineScript.ScriptWorldId.MainWorld,
    usertypes.JsWorld.application: QWebEngineScript.ScriptWorldId.ApplicationWorld,
    usertypes.JsWorld.user: QWebEngineScript.ScriptWorldId.UserWorld,
    usertypes.JsWorld.jseval: QWebEngineScript.ScriptWorldId.UserWorld + 1,
}

# The global WebEngineSettings object
_global_settings = cast('WebEngineSettings', None)

parsed_user_agent: websettings.UserAgent | None = None

_qute_scheme_handler = cast(webenginequtescheme.QuteSchemeHandler, None)
_req_interceptor = cast('interceptor.RequestInterceptor', None)
_download_manager = cast(webenginedownloads.DownloadManager, None)


class _SettingsWrapper:

    """Expose a QWebEngineSettings interface which acts on all profiles.

    For read operations, the default profile value is always used.
    """

    def _default_profile_settings(self):
        profile = profiles.get_registry().get(profiles.DEFAULT_KEY)
        assert profile is not None
        return profile.settings()

    def _settings(self):
        for profile in profiles.get_registry():
            yield profile.settings()

    def setAttribute(self, attribute, on):
        for settings in self._settings():
            settings.setAttribute(attribute, on)

    def setFontFamily(self, which, family):
        for settings in self._settings():
            settings.setFontFamily(which, family)

    def setFontSize(self, fonttype, size):
        for settings in self._settings():
            settings.setFontSize(fonttype, size)

    def setDefaultTextEncoding(self, encoding):
        for settings in self._settings():
            settings.setDefaultTextEncoding(encoding)

    def setUnknownUrlSchemePolicy(self, policy):
        for settings in self._settings():
            settings.setUnknownUrlSchemePolicy(policy)

    def testAttribute(self, attribute):
        return self._default_profile_settings().testAttribute(attribute)

    def fontSize(self, fonttype):
        return self._default_profile_settings().fontSize(fonttype)

    def fontFamily(self, which):
        return self._default_profile_settings().fontFamily(which)

    def defaultTextEncoding(self):
        return self._default_profile_settings().defaultTextEncoding()

    def unknownUrlSchemePolicy(self):
        return self._default_profile_settings().unknownUrlSchemePolicy()


class WebEngineSettings(websettings.AbstractSettings):

    """A wrapper for the config for QWebEngineSettings."""

    _ATTRIBUTES = {
        'content.xss_auditing':
            Attr(QWebEngineSettings.WebAttribute.XSSAuditingEnabled),
        'content.images':
            Attr(QWebEngineSettings.WebAttribute.AutoLoadImages),
        'content.javascript.enabled':
            Attr(QWebEngineSettings.WebAttribute.JavascriptEnabled),
        'content.javascript.can_open_tabs_automatically':
            Attr(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows),
        'content.plugins':
            Attr(QWebEngineSettings.WebAttribute.PluginsEnabled),
        'content.hyperlink_auditing':
            Attr(QWebEngineSettings.WebAttribute.HyperlinkAuditingEnabled),
        'content.local_content_can_access_remote_urls':
            Attr(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls),
        'content.local_content_can_access_file_urls':
            Attr(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls),
        'content.webgl':
            Attr(QWebEngineSettings.WebAttribute.WebGLEnabled),
        'content.local_storage':
            Attr(QWebEngineSettings.WebAttribute.LocalStorageEnabled),
        'content.desktop_capture':
            Attr(QWebEngineSettings.WebAttribute.ScreenCaptureEnabled,
                 converter=lambda val: True if val == 'ask' else val),
        # 'ask' is handled via the permission system

        'input.spatial_navigation':
            Attr(QWebEngineSettings.WebAttribute.SpatialNavigationEnabled),
        'input.links_included_in_focus_chain':
            Attr(QWebEngineSettings.WebAttribute.LinksIncludedInFocusChain),

        'scrolling.smooth':
            Attr(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled),

        'content.print_element_backgrounds':
            Attr(QWebEngineSettings.WebAttribute.PrintElementBackgrounds),

        'content.autoplay':
            Attr(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture,
                 converter=operator.not_),

        'content.dns_prefetch':
            Attr(QWebEngineSettings.WebAttribute.DnsPrefetchEnabled),

        'tabs.favicons.show':
            Attr(QWebEngineSettings.WebAttribute.AutoLoadIconsForPage,
                 converter=lambda val: val != 'never'),
    }

    if machinery.IS_QT6:
        try:
            _ATTRIBUTES['content.canvas_reading'] = Attr(
                QWebEngineSettings.WebAttribute.ReadingFromCanvasEnabled)
        except AttributeError:
            # Added in QtWebEngine 6.6
            pass
        try:
            _ATTRIBUTES['colors.webpage.darkmode.enabled'] = Attr(
                QWebEngineSettings.WebAttribute.ForceDarkMode)
        except AttributeError:
            # Added in QtWebEngine 6.7
            pass

    _FONT_SIZES = {
        'fonts.web.size.minimum':
            QWebEngineSettings.FontSize.MinimumFontSize,
        'fonts.web.size.minimum_logical':
            QWebEngineSettings.FontSize.MinimumLogicalFontSize,
        'fonts.web.size.default':
            QWebEngineSettings.FontSize.DefaultFontSize,
        'fonts.web.size.default_fixed':
            QWebEngineSettings.FontSize.DefaultFixedFontSize,
    }

    _FONT_FAMILIES = {
        'fonts.web.family.standard': QWebEngineSettings.FontFamily.StandardFont,
        'fonts.web.family.fixed': QWebEngineSettings.FontFamily.FixedFont,
        'fonts.web.family.serif': QWebEngineSettings.FontFamily.SerifFont,
        'fonts.web.family.sans_serif': QWebEngineSettings.FontFamily.SansSerifFont,
        'fonts.web.family.cursive': QWebEngineSettings.FontFamily.CursiveFont,
        'fonts.web.family.fantasy': QWebEngineSettings.FontFamily.FantasyFont,
    }

    _UNKNOWN_URL_SCHEME_POLICY = {
        'disallow':
            QWebEngineSettings.UnknownUrlSchemePolicy.DisallowUnknownUrlSchemes,
        'allow-from-user-interaction':
            QWebEngineSettings.UnknownUrlSchemePolicy.AllowUnknownUrlSchemesFromUserInteraction,
        'allow-all':
            QWebEngineSettings.UnknownUrlSchemePolicy.AllowAllUnknownUrlSchemes,
    }

    # Mapping from WebEngineSettings::initDefaults in
    # qtwebengine/src/core/web_engine_settings.cpp
    _FONT_TO_QFONT = {
        QWebEngineSettings.FontFamily.StandardFont: QFont.StyleHint.Serif,
        QWebEngineSettings.FontFamily.FixedFont: QFont.StyleHint.Monospace,
        QWebEngineSettings.FontFamily.SerifFont: QFont.StyleHint.Serif,
        QWebEngineSettings.FontFamily.SansSerifFont: QFont.StyleHint.SansSerif,
        QWebEngineSettings.FontFamily.CursiveFont: QFont.StyleHint.Cursive,
        QWebEngineSettings.FontFamily.FantasyFont: QFont.StyleHint.Fantasy,
    }

    _JS_CLIPBOARD_SETTINGS = {
        'none': {
            QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard: False,
            QWebEngineSettings.WebAttribute.JavascriptCanPaste: False,
        },
        'access': {
            QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard: True,
            QWebEngineSettings.WebAttribute.JavascriptCanPaste: False,
        },
        'access-paste': {
            QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard: True,
            QWebEngineSettings.WebAttribute.JavascriptCanPaste: True,
        },
        'ask': {
            QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard: False,
            QWebEngineSettings.WebAttribute.JavascriptCanPaste: False,
        },
    }

    def set_unknown_url_scheme_policy(
            self, policy: str | usertypes.Unset) -> None:
        """Set the UnknownUrlSchemePolicy to use."""
        if isinstance(policy, usertypes.Unset):
            self._settings.resetUnknownUrlSchemePolicy()
        else:
            new_value = self._UNKNOWN_URL_SCHEME_POLICY[policy]
            self._settings.setUnknownUrlSchemePolicy(new_value)

    def _set_js_clipboard(self, value: str | usertypes.Unset) -> None:
        attr_access = QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard
        attr_paste = QWebEngineSettings.WebAttribute.JavascriptCanPaste

        if isinstance(value, usertypes.Unset):
            self._settings.resetAttribute(attr_access)
            self._settings.resetAttribute(attr_paste)
        else:
            for attr, attr_val in self._JS_CLIPBOARD_SETTINGS[value].items():
                self._settings.setAttribute(attr, attr_val)

    def _update_setting(self, setting, value):
        if setting == 'content.unknown_url_scheme_policy':
            self.set_unknown_url_scheme_policy(value)
        elif setting == 'content.javascript.clipboard':
            self._set_js_clipboard(value)
        # NOTE: When adding something here, also add it to init_settings()!
        super()._update_setting(setting, value)

    def init_settings(self):
        super().init_settings()
        self.update_setting('content.unknown_url_scheme_policy')
        self.update_setting('content.javascript.clipboard')


class ProfileSetter:

    """Helper to set various settings on a profile."""

    def __init__(self, profile):
        self._profile = profile
        self._name_to_method = {
            'content.cache.size': self.set_http_cache_size,
            'content.cookies.store': self.set_persistent_cookie_policy,
            'spellcheck.languages': self.set_dictionary_language,
            'content.headers.user_agent': self.set_http_headers,
            'content.headers.accept_language': self.set_http_headers,
        }

    def update_setting(self, name):
        """Update a setting based on its name."""
        try:
            meth = self._name_to_method[name]
        except KeyError:
            return
        meth()

    def init_profile(self):
        """Initialize settings on the given profile."""
        self.set_http_headers()
        self.set_http_cache_size()
        self._set_hardcoded_settings()
        self.set_persistent_cookie_policy()
        self.set_dictionary_language()
        self.disable_persistent_permissions_policy()

    def _set_hardcoded_settings(self):
        """Set up settings with a fixed value."""
        settings = self._profile.settings()

        settings.setAttribute(
            QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.FocusOnNavigationEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PdfViewerEnabled, False)

    def set_http_headers(self):
        """Set the user agent and accept-language for the given profile.

        We override those per request in the URL interceptor (to allow for
        per-domain values), but this one still gets used for things like
        window.navigator.userAgent/.languages in JS.
        """
        user_agent = websettings.user_agent()
        self._profile.setHttpUserAgent(user_agent)

        accept_language = config.val.content.headers.accept_language
        if accept_language is not None:
            self._profile.setHttpAcceptLanguage(accept_language)

    def set_http_cache_size(self):
        """Initialize the HTTP cache size for the given profile."""
        size = config.val.content.cache.size
        if size is None:
            size = 0
        else:
            size = qtutils.check_overflow(size, 'int', fatal=False)

        # 0: automatically managed by QtWebEngine
        self._profile.setHttpCacheMaximumSize(size)

    def set_persistent_cookie_policy(self):
        """Set the HTTP Cookie size for the given profile."""
        if self._profile.isOffTheRecord():
            return
        if config.val.content.cookies.store:
            value = QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies
        else:
            value = QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies
        self._profile.setPersistentCookiesPolicy(value)

    def set_dictionary_language(self):
        """Load the given dictionaries."""
        filenames = []
        for code in config.val.spellcheck.languages or []:
            local_filename = spell.local_filename(code)
            if not local_filename:
                if not self._profile.isOffTheRecord():
                    message.warning("Language {} is not installed - see "
                                    "scripts/dictcli.py in qutebrowser's "
                                    "sources".format(code))
                continue

            filenames.append(os.path.splitext(local_filename)[0])

        log.config.debug("Found dicts: {}".format(filenames))
        self._profile.setSpellCheckLanguages(filenames)

        should_enable = bool(filenames)
        if self._profile.isSpellCheckEnabled() != should_enable:
            # Only setting conditionally as a WORKAROUND for a bogus Qt error message:
            # https://bugreports.qt.io/browse/QTBUG-131969
            self._profile.setSpellCheckEnabled(should_enable)

    def disable_persistent_permissions_policy(self):
        """Disable webengine's permission persistence."""
        if machinery.IS_QT6:  # for mypy
            try:
                # New in WebEngine 6.8.0
                self._profile.setPersistentPermissionsPolicy(
                    QWebEngineProfile.PersistentPermissionsPolicy.AskEveryTime
                )
            except AttributeError:
                pass


def _update_settings(option):
    """Update global settings when qwebsettings changed."""
    _global_settings.update_setting(option)
    for profile in profiles.get_registry():
        profile.setter.update_setting(option)  # type: ignore[attr-defined]


def _init_user_agent_str(ua):
    global parsed_user_agent
    parsed_user_agent = websettings.UserAgent.parse(ua)
    if parsed_user_agent.upstream_browser_version.endswith(".0.0.0"):
        # https://codereview.qt-project.org/c/qt/qtwebengine/+/616314
        # but we still want the full version available to users if they want it.
        qtwe_versions = version.qtwebengine_versions()
        assert qtwe_versions.chromium is not None
        parsed_user_agent.upstream_browser_version = qtwe_versions.chromium


def init_user_agent():
    """Make the default WebEngine user agent available via parsed_user_agent."""
    actual_default_profile = QWebEngineProfile.defaultProfile()
    assert actual_default_profile is not None
    _init_user_agent_str(actual_default_profile.httpUserAgent())


def _make_script(name, js_code, *,
                 world=QWebEngineScript.ScriptWorldId.ApplicationWorld,
                 injection_point=QWebEngineScript.InjectionPoint.DocumentCreation,
                 subframes=False):
    """Create a script to run early on a page load."""
    script = QWebEngineScript()
    script.setInjectionPoint(injection_point)
    script.setSourceCode(js_code)
    script.setWorldId(world)
    script.setRunsOnSubFrames(subframes)
    script.setName(f'_qute_{name}')
    return script


def _remove_js(scripts, name):
    """Remove an early QWebEngineScript."""
    if machinery.IS_QT6:
        for script in scripts.find(f'_qute_{name}'):
            scripts.remove(script)
    else:  # Qt 5
        script = scripts.findScript(f'_qute_{name}')
        if not script.isNull():
            scripts.remove(script)


def _install_stylesheet(scripts):
    """Install the custom stylesheet.

    Open tabs also get the CSS set directly when the config changes and when
    a search starts or ends.

    Partially inspired by QupZilla:
    https://github.com/QupZilla/qupzilla/blob/v2.0/src/lib/app/mainapplication.cpp#L1063-L1101
    """
    _remove_js(scripts, 'stylesheet')
    css = shared.get_user_stylesheet()
    js_code = javascript.wrap_global(
        'stylesheet',
        resources.read_file('javascript/stylesheet.js'),
        javascript.assemble('stylesheet', 'set_css', css),
    )
    scripts.insert(_make_script('stylesheet', js_code, subframes=True))


@dataclasses.dataclass
class _Quirk:

    filename: str
    injection_point: QWebEngineScript.InjectionPoint = (
        QWebEngineScript.InjectionPoint.DocumentCreation)
    world: QWebEngineScript.ScriptWorldId = QWebEngineScript.ScriptWorldId.MainWorld
    predicate: bool = True
    name: str | None = None

    def __post_init__(self):
        if self.name is None:
            self.name = f"js-{self.filename.replace('_', '-')}"


def _get_quirks():
    """Get a list of all available JS quirks."""
    versions = version.qtwebengine_versions()
    return [
        # FIXME:qt6 Double check which of those are still required
        _Quirk(
            'whatsapp_web',
            injection_point=QWebEngineScript.InjectionPoint.DocumentReady,
            world=QWebEngineScript.ScriptWorldId.ApplicationWorld,
        ),
        _Quirk('discord'),
        _Quirk(
            'googledocs',
            # will be an UA quirk once we set the JS UA as well
            name='ua-googledocs',
        ),

        _Quirk(
            'string_replaceall',
            predicate=versions.webengine < utils.VersionNumber(5, 15, 3),
        ),
        _Quirk(
            'array_at',
            predicate=versions.webengine < utils.VersionNumber(6, 3),
        ),
    ]


def _install_site_specific_quirks(scripts):
    """Install site-specific quirk scripts."""
    for quirk in _get_quirks():
        _remove_js(scripts, f'quirk_{quirk.filename}')

    if not config.val.content.site_specific_quirks.enabled:
        return

    for quirk in _get_quirks():
        if not quirk.predicate:
            continue
        src = resources.read_file(f'javascript/quirks/{quirk.filename}.user.js')
        if quirk.name not in config.val.content.site_specific_quirks.skip:
            scripts.insert(_make_script(
                f'quirk_{quirk.filename}',
                src,
                world=quirk.world,
                injection_point=quirk.injection_point,
            ))


def _install_greasemonkey_scripts(scripts, gm_scripts):
    """Install Greasemonkey scripts, replacing the ones installed before.

    Args:
        scripts: The QWebEngineScriptCollection to install into.
        gm_scripts: A list of GreasemonkeyScripts.
    """
    for script in scripts.toList():
        if script.name().startswith("GM-"):
            log.greasemonkey.debug('Removing script: {}'.format(script.name()))
            removed = scripts.remove(script)
            assert removed, script.name()

    seen_names = set()
    for script in gm_scripts:
        while script.full_name() in seen_names:
            script.dedup_suffix += 1
        seen_names.add(script.full_name())

        new_script = QWebEngineScript()

        try:
            world = int(script.jsworld)
            if not 0 <= world <= qtutils.MAX_WORLD_ID:
                log.greasemonkey.error(
                    f"script {script.name} has invalid value for '@qute-js-world'"
                    f": {script.jsworld}, should be between 0 and "
                    f"{qtutils.MAX_WORLD_ID}")
                continue
        except ValueError:
            try:
                world = JS_WORLD_MAP[usertypes.JsWorld[script.jsworld.lower()]]
            except KeyError:
                log.greasemonkey.error(
                    f"script {script.name} has invalid value for '@qute-js-world'"
                    f": {script.jsworld}")
                continue
        new_script.setWorldId(world)

        # Corresponds to "@run-at document-end" which is the default according to
        # https://wiki.greasespot.net/Metadata_Block#.40run-at - however,
        # QtWebEngine uses QWebEngineScript.InjectionPoint.Deferred (@run-at document-idle) as
        # default.
        #
        # NOTE that this needs to be done before setSourceCode, so that
        # QtWebEngine's parsing of GreaseMonkey tags will override it if there is a
        # @run-at comment.
        new_script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)

        new_script.setSourceCode(script.code())
        new_script.setName(script.full_name())
        new_script.setRunsOnSubFrames(script.runs_on_sub_frames)

        if script.needs_document_end_workaround():
            log.greasemonkey.debug(
                f"Forcing @run-at document-end for {script.name}")
            new_script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)

        log.greasemonkey.debug(f'adding script: {new_script.name()}')
        scripts.insert(new_script)


def _update_greasemonkey_scripts() -> None:
    """Reinstall Greasemonkey scripts on every profile."""
    gm_scripts = greasemonkey.gm_manager.all_scripts()
    for profile in profiles.get_registry():
        _install_greasemonkey_scripts(profile.scripts(), gm_scripts)


def init_greasemonkey() -> None:
    """Install Greasemonkey scripts on all profiles and keep them updated.

    Called after greasemonkey.init(), which runs after the default profile
    exists because it needs the QtNetwork download manager.
    """
    greasemonkey.gm_manager.scripts_reloaded.connect(_update_greasemonkey_scripts)
    _update_greasemonkey_scripts()


def _install_scripts(profile: QWebEngineProfile) -> None:
    """Install qutebrowser's own and Greasemonkey scripts for all pages of a profile.

    These are profile scripts rather than page scripts because QtWebEngine
    6.11+ doesn't run page scripts after a cross-site navigation started by
    the page swaps the renderer process:
    https://github.com/qutebrowser/qutebrowser/issues/8925
    """
    scripts = profile.scripts()
    assert scripts is not None
    js_code = javascript.wrap_global(
        'scripts',
        resources.read_file('javascript/scroll.js'),
        resources.read_file('javascript/webelem.js'),
        resources.read_file('javascript/caret.js'),
    )
    # FIXME:qtwebengine what about subframes=True?
    scripts.insert(_make_script('js', js_code, subframes=True))
    _install_stylesheet(scripts)
    _install_site_specific_quirks(scripts)
    # The default profile is created before Greasemonkey is initialized, and
    # gets its scripts from init_greasemonkey() instead.
    if greasemonkey.gm_manager is not None:
        _install_greasemonkey_scripts(scripts, greasemonkey.gm_manager.all_scripts())


def _update_scripts(option: str) -> None:
    """Reinstall profile scripts depending on a changed option."""
    for profile in profiles.get_registry():
        scripts = profile.scripts()
        assert scripts is not None
        if option in ['scrolling.bar', 'content.user_stylesheets']:
            _install_stylesheet(scripts)
        elif option == 'content.site_specific_quirks.skip':
            _install_site_specific_quirks(scripts)


def _init_profile(profile: QWebEngineProfile) -> profiles.Teardown:
    """Initialize a new QWebEngineProfile.

    Returns a callable undoing the connections made here. The profile registry
    calls it before deleting the profile, so history signals don't keep
    deleted profiles alive.
    """
    # FIXME:mypy subclass QWebEngineProfile instead?
    profile.setter = ProfileSetter(profile)  # type: ignore[attr-defined]
    profile.setter.init_profile()  # type: ignore[attr-defined]

    _qute_scheme_handler.install(profile)
    _req_interceptor.install(profile)
    _download_manager.install(profile)
    cookies.install_filter(profile)

    if notification.bridge is not None:
        notification.bridge.install(profile)

    def clear_visited_link(url: QUrl) -> None:
        profile.clearVisitedLinks([url])

    # Clear visited links on web history clear
    history.web_history.history_cleared.connect(profile.clearAllVisitedLinks)
    history.web_history.url_cleared.connect(clear_visited_link)

    _global_settings.init_settings()
    _maybe_disable_hangouts_extension(profile)
    _install_scripts(profile)

    def teardown() -> None:
        history.web_history.history_cleared.disconnect(profile.clearAllVisitedLinks)
        history.web_history.url_cleared.disconnect(clear_visited_link)

    return teardown


def _maybe_disable_hangouts_extension(profile: QWebEngineProfile) -> None:
    """Disable the Hangouts extension for Qt 6.10+."""
    qtwe_versions = version.qtwebengine_versions(avoid_init=True)
    if not (
        config.val.qt.workarounds.disable_hangouts_extension
        # https://qt-project.atlassian.net/browse/QTBUG-145467
        or qtwe_versions.webengine == utils.VersionNumber(6, 11)
        # earlier hardcoded disables handled in pakjoy.py
    ):
        return

    if machinery.IS_QT6:  # mypy
        try:
            ext_manager = profile.extensionManager()
        except AttributeError:
            return  # added in QtWebEngine 6.10

        if (
            qtwe_versions.webengine
            in [utils.VersionNumber(6, 10, 1), utils.VersionNumber(6, 10, 2)]
            and profile.isOffTheRecord()
        ):
            # WORKAROUND for https://github.com/qutebrowser/qutebrowser/issues/8785
            log.misc.warning(
                "Not disabling Hangouts extension on private profile to avoid "
                "QtWebEngine crash with Qt 6.10.1/.2")
            return

        assert ext_manager is not None  # mypy
        for info in ext_manager.extensions():
            if info.id() == pakjoy.HANGOUTS_EXT_ID:
                log.misc.debug(f"Disabling extension: {info.name()}")
                # setExtensionEnabled(info, False) seems to segfault
                ext_manager.unloadExtension(info)


def _clear_webengine_permissions_json():
    """Remove QtWebEngine's persistent permissions file, if present.

    We have our own permissions feature and don't integrate with their one.
    This only needs to be called when you are on Qt6.8 but PyQt<6.8, since if
    we have access to the `setPersistentPermissionsPolicy()` we will use that
    to disable the Qt feature.
    This needs to be called before we call `setPersistentStoragePath()`
    because Qt will load the file during that.
    """
    for storage_dir in containers.all_storage_dirs():
        permissions_file = storage_dir / "permissions.json"
        try:
            permissions_file.unlink(missing_ok=True)
        except OSError as err:
            log.init.warning(
                f"Error while cleaning up webengine permissions file: {err}"
            )


def default_qt_profile() -> QWebEngineProfile:
    """Get the default profile from Qt."""
    if machinery.IS_QT6:
        return QWebEngineProfile("Default")
    else:
        return QWebEngineProfile.defaultProfile()


def _init_user_agent_and_check_version() -> None:
    """Parse the default user agent and warn about QtWebEngine version mismatches."""
    assert parsed_user_agent is None  # avoid earlier profile initialization
    non_ua_version = version.qtwebengine_versions(avoid_init=True)

    init_user_agent()
    ua_version = version.qtwebengine_versions()

    logger = log.init.warning
    if machinery.IS_QT5:
        # With Qt 5.15, we can't quite be sure about which QtWebEngine patch version
        # we're getting, as ELF parsing might be broken and there's no other way.
        # For most of the code, we don't really care about the patch version though.
        assert (
            non_ua_version.webengine.strip_patch() == ua_version.webengine.strip_patch()
        ), (non_ua_version, ua_version)
        logger = log.init.debug

    if ua_version.webengine != non_ua_version.webengine:
        logger(
            "QtWebEngine version mismatch - unexpected behavior might occur, "
            "please open a bug about this.\n"
            f"  Early version: {non_ua_version}\n"
            f"  Real version:  {ua_version}")


def _create_profile(key: str, private: bool) -> QWebEngineProfile:
    """Create the QWebEngineProfile for a profile key."""
    if private:
        profile = QWebEngineProfile()
        assert profile.isOffTheRecord()
        return profile

    if key == profiles.DEFAULT_KEY:
        profile = default_qt_profile()
        assert not profile.isOffTheRecord()
        profile.setCachePath(os.path.join(standarddir.cache(), 'webengine'))
        profile.setPersistentStoragePath(
            os.path.join(standarddir.data(), 'webengine'))
        return profile

    data_path, cache_path = containers.registry.storage_paths(key)
    profile = QWebEngineProfile(key)
    assert not profile.isOffTheRecord()
    profile.setCachePath(str(cache_path))
    profile.setPersistentStoragePath(str(data_path))
    return profile


def _init_site_specific_quirks():
    """Add custom user-agent settings for problematic sites.

    See https://github.com/qutebrowser/qutebrowser/issues/4810
    """
    if not config.val.content.site_specific_quirks.enabled:
        return

    # Please leave this here as a template for new UAs.
    # default_ua = ("Mozilla/5.0 ({os_info}) "
    #               "AppleWebKit/{webkit_version} (KHTML, like Gecko) "
    #               "{qt_key}/{qt_version} "
    #               "{upstream_browser_key}/{upstream_browser_version_short} "
    #               "Safari/{webkit_version}")
    firefox_ua = "Mozilla/5.0 ({os_info}; rv:149.0) Gecko/20100101 Firefox/149.0"

    # Needed for gitlab.gnome.org which blocks old Chromium versions outright,
    # except when QtWebEngine/... is in the UA.
    #
    # We could further modify the UA to just "qutebrowser" or something so we don't get
    # Anubis at all, but it looks like their Anubis triggers to more than just
    # Mozilla/5.0 (also AppleWebKit/... and Chromium/... possibly?), so at that point
    # I'm not sure if we can strip down the UA so much without breaking
    # something in GitLab as well.
    not_mozilla_ua = (
        "Mozilla/5.0 ({os_info}) AppleWebKit/{webkit_version} (KHTML, like Gecko) "
        "{qt_key}/{qt_version} {upstream_browser_key}/{upstream_browser_version_short} "
        "Safari/{webkit_version}"
    )

    def maybe_newer_chrome_ua(at_least_version):
        """Return a new UA if our current chrome version isn't at least at_least_version."""
        current_chome_version = version.qtwebengine_versions().chromium_major
        if current_chome_version >= at_least_version:
            return None

        return (
            "Mozilla/5.0 ({os_info}) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{at_least_version} "
            "Safari/537.36"
        )

    utils.unused(maybe_newer_chrome_ua)

    user_agents = [
        # Needed to avoid a "you're using a browser [...] that doesn't allow us
        # to keep your account secure" error.
        # https://github.com/qutebrowser/qutebrowser/issues/5182
        ("ua-google", "https://accounts.google.com/*", firefox_ua),
        ("ua-gnome-gitlab", "https://gitlab.gnome.org/*", not_mozilla_ua),
    ]

    for name, pattern, ua in user_agents:
        if not ua:
            continue
        if name not in config.val.content.site_specific_quirks.skip:
            config.instance.set_obj('content.headers.user_agent', ua,
                                    pattern=urlmatch.UrlPattern(pattern),
                                    hide_userconfig=True)

    if 'misc-krunker' not in config.val.content.site_specific_quirks.skip:
        config.instance.set_obj(
            'content.headers.accept_language',
            '',
            pattern=urlmatch.UrlPattern('https://matchmaker.krunker.io/*'),
            hide_userconfig=True,
        )


def _init_default_settings():
    """Set permissions required for internal functionality.

    - Make sure the devtools always get images/JS permissions.
    - On Qt 6, make sure files in the data path can load external resources.
    """
    devtools_settings: list[tuple[str, Any]] = [
        ('content.javascript.enabled', True),
        ('content.images', True),
        ('content.cookies.accept', 'all'),
    ]

    for setting, value in devtools_settings:
        for pattern in ['chrome-devtools://*', 'devtools://*']:
            config.instance.set_obj(setting, value,
                                    pattern=urlmatch.UrlPattern(pattern),
                                    hide_userconfig=True)

    if machinery.IS_QT6:
        userscripts_settings: list[tuple[str, Any]] = [
            ("content.local_content_can_access_remote_urls", True),
            ("content.local_content_can_access_file_urls", False),
        ]
        # https://codereview.qt-project.org/c/qt/qtwebengine/+/375672
        url = pathlib.Path(standarddir.data(), "userscripts").as_uri()
        for setting, value in userscripts_settings:
            config.instance.set_obj(setting,
                                    value,
                                    pattern=urlmatch.UrlPattern(f"{url}/*"),
                                    hide_userconfig=True)


def init():
    """Initialize the global QWebSettings."""
    webenginequtescheme.init()
    spell.init()

    # For some reason we need to keep a reference, otherwise the scheme handler
    # won't work...
    # https://www.riverbankcomputing.com/pipermail/pyqt/2016-September/038075.html
    global _qute_scheme_handler
    app = QApplication.instance()
    log.init.debug("Initializing qute://* handler...")
    _qute_scheme_handler = webenginequtescheme.QuteSchemeHandler(parent=app)

    global _req_interceptor
    log.init.debug("Initializing request interceptor...")
    from qutebrowser.browser.webengine import interceptor
    _req_interceptor = interceptor.RequestInterceptor(parent=app)

    global _download_manager
    log.init.debug("Initializing QtWebEngine downloads...")
    _download_manager = webenginedownloads.DownloadManager(parent=app)
    objreg.register('webengine-download-manager', _download_manager)
    from qutebrowser.misc import quitter
    quitter.instance.shutting_down.connect(_download_manager.shutdown)

    log.init.debug("Initializing notification presenter...")
    notification.init()

    log.init.debug("Initializing global settings...")
    global _global_settings
    _global_settings = WebEngineSettings(_SettingsWrapper())

    log.init.debug("Initializing profiles...")

    # Apply potential resource patches while initializing profiles.
    with pakjoy.patch_webengine():
        _init_user_agent_and_check_version()
        # Must happen before any profile gets a persistent storage path,
        # because Qt loads the file then.
        _clear_webengine_permissions_json()
        profiles.registry = profiles.ProfileRegistry(
            factory=_create_profile, initializer=_init_profile)
        profiles.registry.acquire(profiles.DEFAULT_KEY, private=False)

    config.instance.changed.connect(_update_settings)
    config.instance.changed.connect(_update_scripts)

    log.init.debug("Misc initialization...")
    _init_site_specific_quirks()
    _init_default_settings()


def shutdown():
    pass
