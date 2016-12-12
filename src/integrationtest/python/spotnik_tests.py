#!/usr/bin/env python
from __future__ import print_function, absolute_import, division

import socket

import boto3
import mock
import os
import time
import unittest2

from spotnik.main import main
from spotnik.spotnik import ReplacementPolicy
from subprocess import check_call, call

import boto3


class SpotnikTestsBase(unittest2.TestCase):
    region_name = 'eu-west-1'
    stack_config = 'src/integrationtest/integrationtest_stacks.yaml'

    @classmethod
    def setUpClass(cls):
        cls.ec2 = boto3.client('ec2', region_name=cls.region_name)
        cls.autoscaling = boto3.client('autoscaling', region_name=cls.region_name)

    def assert_spotnik_request_instances(self, amount):
        num_requests_before = self.get_num_spot_requests()
        main()
        # The API of self.ec2 needs some time before the spot requests shows up.
        time.sleep(30)
        num_requests_after = self.get_num_spot_requests()
        delta = num_requests_after - num_requests_before
        self.assertEqual(delta, amount)
        # is service still available
        self.assert_service_is_available()

    def assert_service_is_available(self):
        self.assertTrue(self.is_fully_up_and_running())

    def get_num_spot_requests(self):
        response = self.ec2.describe_spot_instance_requests()
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
        client = boto3.client('elb', region_name=self.region_name)
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
        call("cf delete --confirm " + self.stack_config, shell=True)
        check_call("cf sync --confirm " + self.stack_config, shell=True)
        counter = 0
        while counter < 300 and not self.is_fully_up_and_running():
            counter += 1
            time.sleep(1)
        self.assert_service_is_available()

        if os.environ.get('SKIP_PORT22_TESTS') == 'true':
            time.sleep(300)

    def delete_application_stack(self):
        check_call("cf delete --confirm " + self.stack_config, shell=True)

    def get_cf_output(self):
        client = boto3.client('cloudformation', region_name=self.region_name)
        response = client.describe_stacks(StackName='SimpleElbAppSpotnikIntegrationtest')
        outputs = response['Stacks'][0]['Outputs']
        outputs = {item['OutputKey']: item['OutputValue'] for item in outputs}
        return outputs['elbName'], outputs['elbDnsName'], outputs['asgName']


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
            self.assert_spotnik_request_instances(0)
            _, _, asg_name = self.get_cf_output()
            time.sleep(10)
            asg = self.autoscaling.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])['AutoScalingGroups'][0]
            fake_spotnik = mock.Mock()
            fake_spotnik.ec2_client = self.ec2
            on_demand_instances, spot_instances = ReplacementPolicy(asg, fake_spotnik).get_instances()

            if len(on_demand_instances) == 1:
                break
            time.sleep(20)
        else:
            raise Exception("Timed out waiting for Spotnik to attach the new instance")
        self.assertEqual(len(on_demand_instances), 1)
        self.assertEqual(len(spot_instances), 1)
        self.assertEqual(spot_instances[0]['InstanceType'], 'm3.medium')

        # Fourth run of spotnik should do nothing because number of ondemand instances would fall below minimum.
        self.assert_spotnik_request_instances(0)

        # configute spotnik to not keep any on demand instances
        self.autoscaling.delete_tags(Tags=[{'ResourceId': asg_name, 'ResourceType': 'auto-scaling-group','Key': 'spotnik-min-on-demand-instances'}])
        self.assert_spotnik_request_instances(1)

        # all instances have been spotified
        self.assert_spotnik_request_instances(0)

        self.delete_application_stack()


if __name__ == "__main__":
    unittest2.main()
