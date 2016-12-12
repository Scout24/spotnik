#!/usr/bin/env python
from __future__ import print_function, absolute_import, division

import mock
import time
import unittest2

from spotnik.spotnik import ReplacementPolicy
from spotnik_tests_base import SpotnikTestsBase


class SpotnikTests(SpotnikTestsBase):
    def test_spotnik_main(self):
        self.create_application_stack()

        # First run of spotnik must not create a new spot request,
        # because freshly launched instances should not be replaced
        self.assert_spotnik_request_instances(0)

        # Normally, spotnik only replaces instances that have been running
        # for 45 to 55 minutes. This would make the integration test too
        # long, so deactivate this feature.
        ReplacementPolicy.should_instance_be_replaced_now = lambda x, y: True

        # Second run of spotnik must create exactly one new spot request.
        self.assert_spotnik_request_instances(1)

        # Third run of spotnik should attach the running spot instance to the
        # asg. But only once the spot instance is in state "running", which
        # may take a while.
        for attempt in 1, 2, 3:
            print("Waiting for spot request to start running... %s" % attempt)
            try:
                self.assert_spotnik_request_instances(-1)
                break
            except Exception:
                time.sleep(20)
                continue
        else:
            raise Exception("Timed out waiting for Spotnik to attach the new instance")
        _, _, asg_name = self.get_cf_output()
        asg = self.autoscaling.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])['AutoScalingGroups'][0]
        fake_spotnik = mock.Mock()
        fake_spotnik.ec2_client = self.ec2
        on_demand_instances, spot_instances = ReplacementPolicy(asg, fake_spotnik).get_instances()

        self.assertEqual(len(on_demand_instances), 1)
        self.assertEqual(len(spot_instances), 1)
        self.assertEqual(spot_instances[0]['InstanceType'], 'm3.medium')

        # Fourth run of spotnik should do nothing because number of ondemand instances would fall below minimum.
        self.assert_spotnik_request_instances(0)

        # configute spotnik to not keep any on demand instances
        self.autoscaling.delete_tags(Tags=[{'ResourceId': asg_name, 'ResourceType': 'auto-scaling-group','Key': 'spotnik-min-on-demand-instances'}])
        self.assert_spotnik_request_instances(1)

        # Second spot instance is attached to ASG and gets untagged.
        for attempt in 1, 2, 3:
            print("Waiting for spot request to start running... %s" % attempt)
            try:
                self.assert_spotnik_request_instances(-1)
                break
            except Exception:
                time.sleep(20)
                continue
        else:
            raise Exception("Timed out waiting for Spotnik to attach the new instance")

        # all instances have been spotified
        self.assert_spotnik_request_instances(0)

        self.delete_application_stack()


if __name__ == "__main__":
    unittest2.main()
