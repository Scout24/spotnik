from __future__ import print_function, absolute_import, division

import unittest2

from spotnik.spotnik import _boto_tags_to_dict, ReplacementPolicy

class SpotnikTests(unittest2.TestCase):
    def test_boto_tag_conversion(self):
        boto_tags = [{'Key': 'foo', 'Value': 'bar'}, {'Key': 'ham', 'Value': 'spam'}]
        expected_tags = {'foo': 'bar', 'ham': 'spam'}
        self.assertEqual(_boto_tags_to_dict(boto_tags), expected_tags)

        boto_tags = []
        expected_tags = {}
        self.assertEqual(_boto_tags_to_dict(boto_tags), expected_tags)


class ReplacementPolicyTests(unittest2.TestCase):
    def setUp(self):
        fake_asg = {'AutoScalingGroupName': 'thename', 'Tags': []}
        self.policy = ReplacementPolicy(fake_asg)

    def test_is_replacement_needed_all_spot_no_on_demand(self):
        self.policy.get_instances = lambda: ([], ['spot1', 'spot2'])
        self.assertEqual(self.policy.is_replacement_needed(), False)

    def test_is_replacement_needed_some_spot_some_on_demand(self):
        self.policy.get_instances = lambda: (['od1', 'od2'], ['spot1', 'spot2'])
        self.assertEqual(self.policy.is_replacement_needed(), True)

    def test_is_replacement_needed_no_spot_all_on_demand(self):
        self.policy.get_instances = lambda: (['od1', 'od2'], [])
        self.assertEqual(self.policy.is_replacement_needed(), True)

    def test_is_replacement_needed_min_on_demand_reached(self):
        fake_asg = {'AutoScalingGroupName': 'thename',
                    'Tags': [{'Key': 'spotnik-min-on-demand-instances', 'Value': '2'}]}
        self.policy = ReplacementPolicy(fake_asg)

        self.policy.get_instances = lambda: (['od1', 'od2'], ['spot1'])
        self.assertEqual(self.policy.is_replacement_needed(), False)

    def test_is_replacement_needed_min_on_demand_not_reached(self):
        fake_asg = {'AutoScalingGroupName': 'thename',
                    'Tags': [{'Key': 'spotnik-min-on-demand-instances', 'Value': '2'}]}
        self.policy = ReplacementPolicy(fake_asg)

        self.policy.get_instances = lambda: (['od1', 'od2', 'od3'], ['spot1'])
        self.assertEqual(self.policy.is_replacement_needed(), True)
