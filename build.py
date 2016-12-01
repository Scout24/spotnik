import os

from pybuilder.core import use_plugin, init
from pybuilder.vcs import VCSRevision

use_plugin("python.core")
use_plugin("python.unittest")
use_plugin("python.install_dependencies")
use_plugin("python.flake8")
use_plugin("python.coverage")
use_plugin("python.distutils")
use_plugin("python.integrationtest")

use_plugin('copy_resources')
use_plugin('pypi:pybuilder_aws_plugin')

bucket_name = os.environ.get('BUCKET_NAME_FOR_UPLOAD', 'spotnik-distribution')

name = "spotnik"
default_task = ['clean', 'analyze', 'package']
version = '%s.%s' % (VCSRevision().get_git_revision_count(), os.environ.get('BUILD_NUMBER', '0'))

@init
def set_properties(project):
    project.set_property('coverage_break_build', False)
    project.set_property('integrationtest_inherit_environment', True)
    project.set_property('integrationtest_always_verbose', True)

    project.set_property('bucket_name', bucket_name)

    project.depends_on('boto3')
    project.build_depends_on('unittest2')
    project.build_depends_on('cfn-sphere')


@init(environments='teamcity')
def set_properties_for_teamcity_builds(project):
    project.set_property('teamcity_output', True)
    project.default_task = ['clean', 'install_build_dependencies', 'publish']
    project.set_property('install_dependencies_index_url', os.environ.get('PYPIPROXY_URL'))
    project.set_property('integrationtest_additional_environment', {'SKIP_PORT22_TESTS': 'true'})
