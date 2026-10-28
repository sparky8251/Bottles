project(
	'bottles',
	version: '67.5',
	meson_version: '>= 1.5.0',
	default_options: [
		'warning_level=2',
	],
	license: 'GPL-3.0-only'
)


APP_NAME = 'Bottles'
DEVELOPER_ID = 'com.usebottles'
BASE_ID = DEVELOPER_ID + '.' + meson.project_name()
APP_ID = BASE_ID
APP_VERSION = meson.project_version()
_version_array = APP_VERSION.split('.')
APP_MAJOR_VERSION = _version_array[0]
APP_MINOR_VERSION = _version_array[1]
PROFILE = get_option('profile')

if PROFILE == 'development'
	git_revision = run_command(
		'git', 'rev-parse', '--short', 'HEAD',
		check: false
	)
	if git_revision.returncode() == 0
		APP_VERSION += '-' + git_revision.stdout().strip()
	else
		APP_VERSION += '-devel'
	endif
	APP_NAME += ' (Development)'
	APP_ID += '.' + 'Devel'
endif


gnome = import('gnome')
i18n = import('i18n')
python = import('python')
localedir = get_option('localedir')

# ---------------------------------------------------------------------------
# Build requirements: everything the build itself executes. Nothing below is
# needed at runtime, and nothing at runtime is needed to build.
# ---------------------------------------------------------------------------
py = python.find_installation('python3')
blueprint_compiler = find_program('blueprint-compiler')
glib_compile_resources = find_program('glib-compile-resources')
msgfmt = find_program('msgfmt')

# ---------------------------------------------------------------------------
# Runtime requirements: the single declaration of what the installed program
# needs. The generated bottles-check-deps verifies this list at configure time
# (per the runtime_checks option) and again inside the shipped artifact, so a
# dependency that changes in the code without changing here fails a build.
# ---------------------------------------------------------------------------
runtime_python_modules = [
	'gi',
	'cairo',
	'yaml',
	'pycurl',
	'chardet',
	'requests',
	'markdown',
	'icoextract',
	'patoolib',
	'pathvalidate',
	'pefile',
	'yara',
	'vkbasalt',
]

runtime_typelibs = [
	'GLib=2.0',
	'GObject=2.0',
	'Gio=2.0',
	'Gdk=4.0',
	'GdkPixbuf=2.0',
	'Gtk=4.0',
	'Adw=1',
	'GtkSource=5',
	'Secret=1',
	'Xdp=1.0',
	'XdpGtk4=1.0',
	'Pango=1.0',
	'Graphene=1.0',
]

# Programs the code executes on ordinary paths. '|' separates alternatives of
# which one must exist; an absolute path is accepted as-is.
runtime_programs = [
	'bash',
	'cp',
	'rm',
	'touch',
	'sleep',
	'7z|7za|7zr',
	'cabextract',
	'update-desktop-database',
	'fvs2',
	'vmtouch',
	'umu-run',
]

# Programs behind a feature toggle or a fallback; reported, never fatal.
runtime_optional_programs = [
	'gamemoderun',
	'gamescope|/usr/lib/extensions/vulkan/gamescope/bin/gamescope',
	'mangohud|/usr/lib/extensions/vulkan/MangoHud/bin/mangohud',
	'obs-vkcapture|/usr/lib/extensions/vulkan/OBSVkCapture/bin/obs-vkcapture',
	'xterm',
	'easyterm.py',
	'wine',
]

deps_conf = configuration_data()
deps_conf.set('PYTHON', py.full_path())
deps_conf.set('RUNTIME_PYTHON_MODULES', ' '.join(runtime_python_modules))
deps_conf.set('RUNTIME_TYPELIBS', ' '.join(runtime_typelibs))
deps_conf.set('RUNTIME_PROGRAMS', ' '.join(runtime_programs))
deps_conf.set('RUNTIME_OPTIONAL_PROGRAMS', ' '.join(runtime_optional_programs))

# The checker is a validation tool, not part of the program. Release builds
# ship without it; the development profile installs it so the testing
# artifact can be verified against the runtime it actually runs on.
check_deps = configure_file(
	input: 'build-aux/bottles-check-deps.py.in',
	output: 'bottles-check-deps',
	configuration: deps_conf,
	install: PROFILE == 'development',
	install_dir: get_option('bindir'),
	install_mode: 'rwxr-xr-x',
)

runtime_checks = get_option('runtime_checks')
if not runtime_checks.disabled()
	check_result = run_command(py, check_deps, '--strict', check: false)
	message('\n' + check_result.stdout())
	if check_result.returncode() != 0
		if runtime_checks.enabled()
			error('runtime dependency check failed; fix the environment or pass -Druntime_checks=disabled for a build stage that is not the runtime')
		else
			warning('runtime dependency check failed; the installed program will not work unless the runtime environment provides what is listed above')
		endif
	endif
endif

summary({
	'python': py.full_path(),
	'blueprint-compiler': blueprint_compiler.full_path(),
	'glib-compile-resources': glib_compile_resources.full_path(),
	'msgfmt': msgfmt.full_path(),
}, section: 'Build tools')
if runtime_checks.disabled()
	runtime_checks_mode = 'disabled'
elif runtime_checks.enabled()
	runtime_checks_mode = 'enabled (missing is fatal)'
else
	runtime_checks_mode = 'auto (missing warns)'
endif
summary({
	'runtime_checks': runtime_checks_mode,
	'checker installed': PROFILE == 'development',
}, section: 'Runtime dependencies')

subdir('po')
subdir('bottles')
subdir('data')

gnome.post_install(
	glib_compile_schemas: true,
	gtk_update_icon_cache: true,
	update_desktop_database: true,
)
