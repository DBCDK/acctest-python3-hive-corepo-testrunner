#!/usr/bin/env python
# -*- coding: utf-8 -*-
# -*- mode: python -*-
"""
:mod:`acceptance_tester.testsuite_runners.hive_corepo.testrunner` -- Testrunner for hive-corepo
===============================================================================================

======================
Hive-corepo Testrunner
======================

This class executes xml test files of type 'hive-corepo'.
"""
import logging
import os
import pprint
import shutil
import subprocess
from nose.tools import nottest


from acceptance_tester.abstract_testsuite_runner.test_runner import TestRunner as AbstractTestRunner

from os_python.corepo.corepo import Corepo
from os_python.corepo.corepo_parser import CorepoParser

from os_python.hive.hive_parser import HiveParser

from os_python.openagency_parser import OpenAgencyParser

from os_python.connectors.hive import HiveDockerConnector
from os_python.connectors.openagency_mock import OpenAgencyMock

from os_python.common.utils.init_functions import NullHandler
from os_python.common.utils.cleanupstack import CleanupStack


### define logger
logger = logging.getLogger( "dbc." + __name__ )
logger.addHandler( NullHandler() )


class TestRunner( AbstractTestRunner ):

    @nottest
    def run_test( self, test_xml, build_folder, resource_manager ):
        """
        Runs a 'hive-corepo' test.

        This method runs a test and puts the result into the
        failure/error lists accordingly.

        :param test_xml:
            Xml object specifying test.
        :type test_xml:
            lxml.etree.Element
        :param build_folder:
            Folder to use as build folder.
        :type build_folder:
            string
        :param resource_manager:
            Class used to secure reources.
        :type resource_manager:
            class that inherits from
            acceptance_tester.abstract_testsuite_runner.resource_manager

        """
        container_suite = resource_manager.container_pool.take()
        try:
            corepo_db = container_suite.get("corepo-db", build_folder)
            corepo_content_service = container_suite.get("corepo-content-service", build_folder)
            hive = container_suite.get("hive", build_folder)
            wiremock = container_suite.get("wiremock", build_folder)

            # connectors
            ingest_tool = os.path.join(resource_manager.resource_folder, 'corepo-ingest.jar')
            corepo_db_root = "corepo:corepo@%s:5432/corepo" % corepo_db.get_ip()

            corepo_connector = Corepo(corepo_db, corepo_content_service, ingest_tool, os.path.join(build_folder, 'ingest'))

            hive_connector = HiveDockerConnector(hive)
            openagency_connector = OpenAgencyMock("http://%s:8080" % wiremock.get_ip(), proxy="https://openagency.addi.dk/test_2.34/")

            ### Setup parsers
            self.parser_functions.update(CorepoParser(self.base_folder, corepo_connector).parser_functions)
            self.parser_functions.update(HiveParser(self.base_folder, hive_connector).parser_functions)
            self.parser_functions.update(OpenAgencyParser(openagency_connector).parser_functions)

            ### run the test
            stop_stack = CleanupStack.getInstance()
            try:
                for service, name in [(corepo_db, 'corepo-db'),
                                      (corepo_content_service, 'corepo-content-service'),
                                      (hive, 'hive'),
                                      (wiremock, 'wiremock'),
                                      (corepo_connector, 'corepo')]:
                    stop_stack.addFunction(self.save_service_logfiles, service, name)
                corepo_connector.start()

                self.parse( test_xml )

            finally:
                stop_stack.callFunctions()

        except Exception as err:
            logger.error( "Caught error during testing: %s"%str(err))
            raise

        finally:
            resource_manager.container_pool.release(container_suite)
