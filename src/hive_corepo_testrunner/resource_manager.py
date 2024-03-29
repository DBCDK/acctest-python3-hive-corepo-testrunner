#!/usr/bin/env python
# -*- coding: utf-8 -*-
# -*- mode: python -*-
"""
:mod:`acceptance_tester.testsuite_runners.hive_corepo.resource_manager` -- Resource manager for hive-corepo
===========================================================================================================

============================
Hive-corepo resource manager
============================

Resource Manager for hive-service/corepo-repository integration
testing.
"""
import logging
import hashlib
import os
import subprocess
import time

from os_python.common.net.iserver import IServer
from os_python.common.utils.init_functions import die
from acceptance_tester.abstract_testsuite_runner.resource_manager import AbstractResourceManager
from os_python.common.utils.init_functions import NullHandler
from os_python.docker.docker_container import DockerContainer
from os_python.docker.docker_container import ContainerSuitePool
from os_python.connectors.postgres import PostgresDockerConnector

from os_python.wiremock_helper import wiremock_load_vipcore_from_dir

from configobj import ConfigObj

### define logger
logger = logging.getLogger( "dbc."+__name__ )
logger.addHandler( NullHandler() )

class ContainerPoolImpl(ContainerSuitePool):

    def __init__(self, resource_folder, resource_config):
        super(ContainerPoolImpl, self).__init__()
        self.resource_folder = resource_folder
        self.resource_config = resource_config

    def create_suite(self, suite):
        suite_name = "_hive_corepo_%f" % time.time()

        corepo_db = suite.create_container("corepo-db", image_name=DockerContainer.secure_docker_image('corepo-postgresql-1.4'),
                             name="corepo-db" + suite_name,
                             environment_variables={"POSTGRES_USER": "corepo",
                                                    "POSTGRES_PASSWORD": "corepo",
                                                    "POSTGRES_DB": "corepo"},
                             start_timeout=1200)


        wiremock = suite.create_container("wiremock", image_name=DockerContainer.secure_docker_image('wiremock'),
                             name="wiremock" + suite_name,
                             start_timeout=1200)
        wiremock.start()
        corepo_db.start()
        corepo_db.waitFor("database system is ready to accept connections")
        wiremock.waitFor("verbose:")
        vip_url = "http://%s:8080" % wiremock.get_ip()

        corepo_db_root = "corepo:corepo@%s:5432/corepo" % corepo_db.get_ip()

        wiremock_load_vipcore_from_dir("http://%s:8080" % wiremock.get_ip(), self.resource_folder)

        corepo_content_service = suite.create_container("corepo-content-service",
                                                        image_name=DockerContainer.secure_docker_image('corepo-content-service-1.4'),
                                                        name="corepo-content-service" + suite_name,
                                                        environment_variables={"COREPO_POSTGRES_URL": corepo_db_root,
                                                                               "VIPCORE_ENDPOINT": vip_url,
                                                                               "LOG__dk_dbc": "DEBUG",
                                                                               "JAVA_MAX_HEAP_SIZE": "2G",
                                                                               "PAYARA_STARTUP_TIMEOUT": 1200},
                                                        start_timeout=1200)

        volumes = None
        hive_jsar = '/nashorn-js-hive.jsar'
        if 'local_javascript' in self.resource_config:
            volumes = {self.resource_config['local_javascript']: '/local.jsar'}
            hive_jsar = '/local.jsar'

        hive_env_vars = {"REPOSITORY_URL": "jdbc:postgresql://corepo:corepo@%s:5432/corepo" % corepo_db.get_ip(),
                        "HARVEST_MODE": "SERVER",
                        "HARVEST_HARVESTER": "ESFileRecordFeeder",
                        "ADDISERVICE_URL": "",
                        "BATCHEXCHANGE_JDBCURL": "",
                        "VIPCORE_ENDPOINT": vip_url,
                        "HIVE_POOLSIZE": 1,
                        "HIVE_PROCESSORJSAR": hive_jsar,
                        "HARVEST_POLLINTERVAL":2,
                        "LOG__JavaScript_Logger": "TRACE",
                        "LOG__dk_dbc_opensearch_hive": "DEBUG",
                        "LOG__dk_dbc": "INFO"}

        hive = suite.create_container("hive",
                                      image_name=DockerContainer.secure_docker_image('hive-app'),
                                      name="hive" + suite_name,
                                      environment_variables=hive_env_vars,
                                      volumes=volumes,
                                      start_timeout=1200)

        corepo_content_service.start()
        hive.start()

        # Service is not ready until both are done:
        corepo_content_service.waitFor("was successfully deployed in")
        corepo_content_service.waitFor(") ready in ")

        hive.waitFor("Harvesting available records")

    def on_release(self, name, container):
        if name == "corepo-db":
            connector = PostgresDockerConnector(container)
            connector.wipe("records", "corepo")
            connector.restart_sequence("work_id_seq", "corepo")
            connector.restart_sequence("unit_id_seq", "corepo")

class ResourceManager( AbstractResourceManager ):

    def __init__( self, resource_folder, tests, use_preloaded, use_config, conf_file=None ):
        logger.info( "Securing necessary resources." )
        self.tests = tests

        self.resource_folder = resource_folder
        if not os.path.exists( self.resource_folder ):
            os.mkdir( self.resource_folder )

        self.use_preloaded_resources = use_preloaded

        self.use_config_resources = use_config

        self.resource_config = ConfigObj(self.use_config_resources)

        self.container_pool = ContainerPoolImpl(resource_folder, self.resource_config)

        self.required_artifacts = {'wiremock-vipcore': ['wiremock-vipcore.zip', 'os-wiremock-rules'], 'corepo-ingest': ['corepo-ingest.jar', 'corepo/job/master']}
        for artifact in self.required_artifacts:
            self.required_artifacts[artifact].append(self._secure_artifact(artifact, *self.required_artifacts[artifact]))

    def shutdown(self):
        self.container_pool.shutdown()

    def _secure_artifact(self, name, artifact, project, build_number=None):
        if name in self.resource_config:
            logger.debug("configured resource %s at %s" % (name, self.resource_config[name]))
            return self.resource_config[name]

        if self.use_preloaded_resources == False:
            logger.debug( "Downloading %s artifact from integration server"%artifact )
            iserv = IServer( temp_folder=self.resource_folder, project_name=project )

            return iserv.download_and_validate_artifact( self.resource_folder, artifact, build_number=build_number)

        logger.debug("Using preloaded %s artifact"%artifact)
        preloaded_artifact = os.path.join(self.resource_folder, artifact)

        return preloaded_artifact

