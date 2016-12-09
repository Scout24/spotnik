from __future__ import print_function, absolute_import, division

import unittest2

from mock import Mock, patch

from spotnik.main import main

class MainTests(unittest2.TestCase):
    @patch("spotnik.main.get_aws_region_names")
    @patch("spotnik.main.Spotnik")
    def test_main_fails_if_regional_thread_fails(self, mock_spotnik, mock_get_aws_region_names):
        # Since most work is done in threads, the main function needs to
        #   - make sure it gets notified about errors
        #   - propagates the error to its caller
        mock_get_aws_region_names.return_value = ['region_one', 'region_two']
        mock_spotnik.get_spotnik_asgs.side_effect = Exception
        self.assertRaises(Exception, main)
