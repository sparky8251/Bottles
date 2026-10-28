pkgdatadir = join_paths(get_option('prefix'), get_option('datadir'), meson.project_name())
executorsdir = join_paths(pkgdatadir, 'bottles/backend/steps/executors')

bottles_sources = [
  '__init__.py',
  'bottle.py',
  'umu.py',
]

install_data(bottles_sources, install_dir: executorsdir)
