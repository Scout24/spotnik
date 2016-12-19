from __future__ import print_function, absolute_import, division

import time
import unittest2

from mock import patch

from spotnik.main import main


def fail_eventually(*args, **kwargs):
    # Test that the thread is really join()ed and it's not just
    # a race condition that makes the test pass.
    time.sleep(.1)
    raise Exception


class MainTests(unittest2.TestCase):
    @patch("spotnik.main.get_aws_region_names")
    @patch("spotnik.main.Spotnik")
    def test_main_fails_if_regional_thread_fails(self, mock_spotnik, mock_get_aws_region_names):
        # Since most work is done in threads, the main function needs to
        #   - make sure it gets notified about errors
        #   - propagates the error to its caller
        mock_get_aws_region_names.return_value = ['region_one', 'region_two']
        mock_spotnik.get_spotnik_asgs.side_effect = fail_eventually
        self.assertRaises(Exception, main)

    @patch("spotnik.main.get_aws_region_names")
    @patch("spotnik.main.Spotnik")
    def test_main_fails_if_asg_thread_fails(self, mock_spotnik, mock_get_aws_region_names):
        mock_get_aws_region_names.return_value = ['region_one', 'region_two']
        mock_spotnik.get_spotnik_asgs.return_value = [{'AutoScalingGroupName': 'foo'}]
        mock_spotnik.side_effect = fail_eventually
        self.assertRaises(Exception, main)
