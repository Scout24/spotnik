#!/usr/bin/env python
from __future__ import print_function, absolute_import, division

import socket

import boto3
import os
import time
import unittest2

from spotnik.spotnik import main, EC2, AUTOSCALING, ReplacementPolicy
from subprocess import check_call, call


class SpotnikTests(unittest2.TestCase):
    def test_spotnik_main(self):
        self.create_application_stack()

        # First run of spotnik must create exactly one new spot request.
        self.assert_spotnik_request_instances(1)

        # second run of spotnik should attach the running spot instance to the asg
        self.assert_spotnik_request_instances(0)
        _, _, asg_name = self.get_cf_output()
        time.sleep(10)
        asg = AUTOSCALING.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])['AutoScalingGroups'][0]
        on_demand_instances, spot_instances = ReplacementPolicy(asg).get_instances()
        self.assertEqual(len(on_demand_instances), 1)
        self.assertEqual(len(spot_instances), 1)
        self.assertEqual(spot_instances[0]['InstanceType'], 'm3.large')

        # Third run of spotnik should do nothing because number of ondemand instances would fall below minimum.
        self.assert_spotnik_request_instances(0)

        # configute spotnik to not keep any on demand instances
        AUTOSCALING.delete_tags(Tags=[{'ResourceId': asg_name, 'ResourceType': 'auto-scaling-group','Key': 'spotnik-min-on-demand-instances'}])
        self.assert_spotnik_request_instances(1)

        # all instances have been spotified
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
        self.assertTrue(self.is_fully_up_and_running())

    @staticmethod
    def get_num_spot_requests():
        response = EC2.describe_spot_instance_requests()
        return len(response['SpotInstanceRequests'])


    def is_port22_reachable(self):
        sock = None
        try:
            _, elb_dns_name, _ = self.get_cf_output()
            port = 22
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.5)
            sock.connect((elb_dns_name, port))
            sock.sendall('Hello, world')
            data = sock.recv(1024)
            print(data)
        except Exception:
            return False
        finally:
            if sock:
                sock.close()
        return 'SSH' in data.upper()

    def is_one_asg_instance_healthy(self):
        elb_name, _, _ = self.get_cf_output()
        client = boto3.client('elb', region_name='eu-west-1')
        response = client.describe_instance_health(LoadBalancerName=elb_name)

        for instance_state in response['InstanceStates']:
            if instance_state['State'] == "InService":
                print("Instance {InstanceId} is in state {State}: {Description}".format(**instance_state))
                return True

        print("ASG is NOT healthy")
        return False

    def is_fully_up_and_running(self):
        if not self.is_one_asg_instance_healthy():
            return False

        if os.environ.get('SKIP_PORT22_TESTS') == 'true':
            print("Skipping service availability test, as configured")
            return True

        return self.is_port22_reachable()


    def create_application_stack(self):
        call("cf delete --confirm src/integrationtest/integrationtest_stacks.yaml", shell=True)
        check_call("cf sync --confirm src/integrationtest/integrationtest_stacks.yaml", shell=True)
        counter = 0
        while counter < 300 and not self.is_fully_up_and_running():
            counter += 1
            time.sleep(1)
        self.assert_service_is_available()

        if os.environ.get('SKIP_PORT22_TESTS') == 'true':
            time.sleep(300)

    def delete_application_stack(self):
        check_call("cf delete --confirm src/integrationtest/integrationtest_stacks.yaml", shell=True)

    @staticmethod
    def get_cf_output():
        client = boto3.client('cloudformation', region_name="eu-west-1")
        response = client.describe_stacks(StackName='SimpleElbAppSpotnikIntegrationtest')
        outputs = response['Stacks'][0]['Outputs']
        outputs = {item['OutputKey']: item['OutputValue'] for item in outputs}
        return outputs['elbName'], outputs['elbDnsName'], outputs['asgName']


if __name__ == "__main__":
    unittest2.main()
