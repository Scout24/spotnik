#!/usr/bin/env python
from __future__ import print_function, absolute_import, division

import unittest2

from spotnik.spotnik import _boto_tags_to_dict, ReplacementPolicy
from subprocess import check_call, call

class SpotnikTests(unittest2.TestCase):
    def test_foo(self):
        self.create_application_stack()
        self.delete_application_stack()

    def create_application_stack(self):
        call("cf delete --confirm src/integrationtest/integrationtest_stacks.yaml", shell=True)
        check_call("cf sync --confirm src/integrationtest/integrationtest_stacks.yaml", shell=True)

    def delete_application_stack(self):
        check_call("cf delete --confirm src/integrationtest/integrationtest_stacks.yaml", shell=True)


if __name__ == "__main__":
    unittest2.main()
