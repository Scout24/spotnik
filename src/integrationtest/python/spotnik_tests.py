#!/usr/bin/env python
from __future__ import print_function, absolute_import, division

import socket

import boto3
import time
import unittest2

from spotnik.spotnik import main, EC2, AUTOSCALING, ReplacementPolicy
from subprocess import check_call, call

class SpotnikTests(unittest2.TestCase):
    def test_spotnik_main(self):
        self.create_application_stack()
        self.assert_service_is_available()

        # First run of spotnik must create exactly one new spot request.
        self.assert_spotnik_request_instances(1)

        # second run of spotnik should attach the running spot instance to the asg
        self.assert_spotnik_request_instances(0)
        elb_dns_name, asg_name = self.get_cf_output()
        asg = AUTOSCALING.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])['AutoScalingGroups'][0]
        on_demand_instances, spot_instances = ReplacementPolicy(asg).get_instances()
        self.assertEqual(len(on_demand_instances), 1)
        self.assertEqual(len(spot_instances), 1)
        self.assertEqual(spot_instances[0]['InstanceType'], 'm3.large')

        # Third run of spotnik should do nothing because number of ondemand instances would fall below minimum.
        self.assert_spotnik_request_instances(0)

        self.delete_application_stack()

    def assert_spotnik_request_instances(self, amount):
        num_requests_before = self.get_num_spot_requests()
        main()
        # The API of EC2 needs some time before the spot requests shows up.
        time.sleep(30)
        num_requests_after = self.get_num_spot_requests()
        delta = num_requests_after - num_requests_before
        self.assertEqual(delta, amount)
        # is service still available
        self.assert_service_is_available()

    def assert_service_is_available(self):
        elb_dns_name, asg_name = self.get_cf_output()
        port = 22
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((elb_dns_name, port))
        s.sendall('Hello, world')
        data = s.recv(1024)
        print(data)
        s.close()
        self.assertIn('SSH', data.upper())

    def get_num_spot_requests(self):
        response = EC2.describe_spot_instance_requests()
        return len(response['SpotInstanceRequests'])

    def create_application_stack(self):
        call("cf delete --confirm src/integrationtest/integrationtest_stacks.yaml", shell=True)
        check_call("cf sync --confirm src/integrationtest/integrationtest_stacks.yaml", shell=True)

    def delete_application_stack(self):
        check_call("cf delete --confirm src/integrationtest/integrationtest_stacks.yaml", shell=True)

    def get_cf_output(self):
        client = boto3.client('cloudformation', region_name="eu-west-1")
        response = client.describe_stacks(StackName='SimpleElbAppSpotnikIntegrationtest')
        outputs = response['Stacks'][0]['Outputs']
        outputs = {item['OutputKey']: item['OutputValue'] for item in outputs}
        return outputs['elbDnsName'], outputs['asgName']


if __name__ == "__main__":
    unittest2.main()
