from __future__ import print_function, absolute_import, division

import boto3
import os
import socket
import tempfile
import time
import unittest2
import yaml
from subprocess import check_call, call

import spotnik
from spotnik.main import main
from spotnik.util import _boto_tags_to_dict


class SpotnikTestsBase(unittest2.TestCase):
    region_name = 'eu-west-1'

    # Must be set by child classes
    stack_config = None
    stack_name = None

    @classmethod
    def setUpClass(cls):
        cls.ec2 = boto3.client('ec2', region_name=cls.region_name)
        cls.autoscaling = boto3.client('autoscaling', region_name=cls.region_name)

    def assert_spotnik_request_instances(self, amount):
        num_requests_before = self.get_num_pending_spot_requests()
        main()
        # The API of EC2 needs some time before the spot requests shows up.
        time.sleep(30)
        num_requests_after = self.get_num_pending_spot_requests()
        delta = num_requests_after - num_requests_before
        self.assertEqual(delta, amount)
        # is service still available
        self.assert_service_is_available()

    def assert_service_is_available(self):
        self.assertTrue(self.is_fully_up_and_running())

    def get_num_pending_spot_requests(self):
        response = self.ec2.describe_spot_instance_requests()
        spot_requests = response['SpotInstanceRequests']
        request_tags = [_boto_tags_to_dict(request.get('Tags', {})) for request in spot_requests]

        _, _, asg_name = self.get_cf_output()

        return sum([asg_name in tags.values() for tags in request_tags])

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

    def _get_current_config(self):
        with open(self.stack_config) as stack_config:
            return yaml.safe_load(stack_config)

    def _set_new_config(self, config):
        dirname = os.path.dirname(self.stack_config)
        individual_stack_config = tempfile.NamedTemporaryFile(
                dir=dirname, delete=False, prefix=self.stack_name + "_")
        yaml.dump(config, stream=individual_stack_config)
        individual_stack_config.close()

        self.stack_config = individual_stack_config.name

    def create_individual_config(self):
        config = self._get_current_config()

        new_stack_name = self.stack_name + str(int(time.time()))
        stack_config = config['stacks'].pop(self.stack_name)
        stack_config['parameters']['spotnikTagKey'] = new_stack_name
        config['stacks'][new_stack_name] = stack_config
        self.stack_name = new_stack_name
        print("Stack/Tag name for test {}: {}".format(
                self.__class__.__name__, new_stack_name))

        self._set_new_config(config)

        # Direct our Spotnik to the correct ASG. By convention, the tag is
        # identical to the name of the stack.
        spotnik.spotnik.SPOTNIK_TAG_KEY = self.stack_name

    def create_application_stack(self):
        self.create_individual_config()

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
        os.unlink(self.stack_config)

    def get_cf_output(self):
        client = boto3.client('cloudformation', region_name=self.region_name)
        response = client.describe_stacks(StackName=self.stack_name)
        outputs = response['Stacks'][0]['Outputs']
        outputs = {item['OutputKey']: item['OutputValue'] for item in outputs}
        return outputs['elbName'], outputs['elbDnsName'], outputs['asgName']
