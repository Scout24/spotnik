from pybuilder.core import use_plugin, init

use_plugin("python.core")
use_plugin("python.unittest")
use_plugin("python.install_dependencies")
use_plugin("python.flake8")
use_plugin("python.coverage")
use_plugin("python.distutils")
use_plugin("python.integrationtest")


name = "spotnik"
default_task = "analyze"
version = "0.1"

@init
def set_properties(project):
    project.set_property('coverage_break_build', False)
    project.set_property('integrationtest_inherit_environment', True)
    project.set_property('integrationtest_always_verbose', True)


    project.depends_on('boto3')
    project.build_depends_on('unittest2')
    project.build_depends_on('cfn-sphere')


@init(environments='teamcity')
def set_properties_for_teamcity_builds(project):
    import os
    project.set_property('teamcity_output', True)
    project.version = '%s-%s' % (project.version, os.environ.get('BUILD_NUMBER', 0))
    project.default_task = ['clean', 'install_build_dependencies', 'publish']
    project.set_property('install_dependencies_index_url', os.environ.get('PYPIPROXY_URL'))
