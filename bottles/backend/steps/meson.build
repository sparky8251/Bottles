pkgdatadir = join_paths(get_option('prefix'), get_option('datadir'), meson.project_name())
stepsdir = join_paths(pkgdatadir, 'bottles/backend/steps')

subdir('executors')

bottles_sources = [
  '__init__.py',
  '__main__.py',
  'conformance.py',
  'grammar.py',
  'machine.py',
  'yaml_frontend.py',
]

install_data(bottles_sources, install_dir: stepsdir)
