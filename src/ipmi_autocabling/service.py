"""Main IPMI Auto-Cabling service."""

import logging
import time
from dataclasses import dataclass
from typing import Optional

from .config import Config
from .correlator import Correlator, CorrelationResult
from .fdb_collector import FDBCollector, FDBEntry
from .mac_utils import normalize_mac
from .netbox_client import NetBoxClient
from .port_classifier import PortClassifier
from .state_db import MACStatus, StateDB

logger = logging.getLogger(__name__)


@dataclass
class RunSummary:
    """Summary of a single run."""

    total_ipmi: int = 0
    created: int = 0
    exists: int = 0
    skipped: int = 0
    ambiguous: int = 0
    not_found: int = 0
    pending: int = 0
    errors: int = 0
    mismatch: int = 0

    def __str__(self) -> str:
        parts = [
            f"Run Summary: total={self.total_ipmi}",
            f"created={self.created}",
            f"exists={self.exists}",
            f"skipped={self.skipped}",
            f"ambiguous={self.ambiguous}",
            f"not_found={self.not_found}",
            f"pending={self.pending}",
            f"errors={self.errors}",
        ]
        if self.mismatch > 0:
            parts.append(f"MISMATCH={self.mismatch}")
        return ", ".join(parts)


class IPMIAutoCablingService:
    """Main service for IPMI auto-cabling."""

    def __init__(self, config: Config):
        self.config = config
        self.netbox = NetBoxClient(config)
        self.fdb_collector = FDBCollector(config)
        self.port_classifier = PortClassifier(config)
        self.state_db = StateDB(config.state_db_path)
        self.correlator = Correlator(
            config=config,
            netbox=self.netbox,
            state_db=self.state_db,
            port_classifier=self.port_classifier,
        )

    def run_once(self) -> RunSummary:
        """Run single pass of auto-cabling."""
        logger.info("Starting OOB auto-cabling run")
        summary = RunSummary()

        try:
            oob_interfaces = self.netbox.get_devices_with_oob()
            summary.total_ipmi = len(oob_interfaces)
            logger.info(f"Found {summary.total_ipmi} devices with OOB IP")

            if not oob_interfaces:
                logger.warning("No devices with OOB IP found, nothing to do")
                return summary

            # Собираем уникальные сайты из найденных устройств
            sites = {oob.site for oob in oob_interfaces if oob.site}
            logger.info(f"Devices found on sites: {sites}")

            # Получаем коммутаторы только для этих сайтов
            switches = self.netbox.get_switches(sites=sites if sites else None)
            logger.info(f"Found {len(switches)} switches to poll")

            if not switches:
                logger.warning("No switches found, cannot collect FDB")
                return summary

            all_fdb_entries: list[FDBEntry] = []
            for switch in switches:
                entries = self.fdb_collector.collect_fdb(
                    switch_name=switch.name,
                    switch_ip=switch.primary_ip or "",
                )
                all_fdb_entries.extend(entries)

            logger.info(f"Collected {len(all_fdb_entries)} FDB entries total")

            results = self.correlator.correlate(
                ipmi_interfaces=oob_interfaces,
                fdb_entries=all_fdb_entries,
                switches=switches,
            )

            for result in results:
                self._process_result(result, summary)

            self.state_db.record_run(
                total_macs=summary.total_ipmi,
                created=summary.created,
                exists=summary.exists,
                skipped=summary.skipped,
                ambiguous=summary.ambiguous,
                not_found=summary.not_found,
                errors=summary.errors,
            )

            logger.info(str(summary))
            return summary

        except Exception as e:
            logger.exception(f"Run failed with error: {e}")
            raise

    def _process_result(self, result: CorrelationResult, summary: RunSummary):
        """Process single correlation result."""
        mac = result.mac
        device_name = result.ipmi_interface.device_name
        iface_name = result.ipmi_interface.interface_name

        log_extra = {
            "mac": mac,
            "device": device_name,
            "interface": iface_name,
            "status": result.status.value,
        }

        if result.switch_name:
            log_extra["switch"] = result.switch_name
            log_extra["port"] = result.port_name

        if result.status == MACStatus.MISMATCH:
            summary.mismatch += 1
            logger.warning(
                f"{device_name}:{iface_name} - MAC MISMATCH! "
                f"Expected {result.expected_mac} but found {result.actual_mac} "
                f"on {result.switch_name}:{result.port_name}",
                extra=log_extra,
            )

        elif result.status == MACStatus.EXISTS:
            summary.exists += 1
            logger.info(f"{device_name}:{iface_name} - cable already exists", extra=log_extra)

        elif result.status == MACStatus.NOT_FOUND:
            summary.not_found += 1
            logger.info(f"{device_name}:{iface_name} - MAC not found in FDB", extra=log_extra)

        elif result.status == MACStatus.AMBIGUOUS:
            summary.ambiguous += 1
            logger.warning(f"{device_name}:{iface_name} - {result.reason}", extra=log_extra)

        elif result.status == MACStatus.SKIP_NON_ACCESS:
            summary.skipped += 1
            logger.info(f"{device_name}:{iface_name} - skipped: {result.reason}", extra=log_extra)

        elif result.status == MACStatus.ERROR:
            summary.errors += 1
            logger.error(f"{device_name}:{iface_name} - error: {result.reason}", extra=log_extra)

        elif result.status == MACStatus.PENDING:
            if result.is_stable and result.port_id:
                cable = self._create_cable(result)
                if cable:
                    summary.created += 1
                    self.state_db.update_status(mac, MACStatus.CREATED, cable.get("id"))
                    status_str = self.config.cable_status.upper()
                    logger.info(
                        f"{device_name}:{iface_name} - cable CREATED ({status_str}) to {result.switch_name}:{result.port_name}",
                        extra=log_extra,
                    )
                else:
                    summary.errors += 1
                    self.state_db.update_status(mac, MACStatus.ERROR)
            else:
                summary.pending += 1
                logger.info(
                    f"{device_name}:{iface_name} - waiting for stability ({result.stability_count}/{self.config.stability_runs})",
                    extra=log_extra,
                )

    def _create_cable(self, result: CorrelationResult) -> Optional[dict]:
        """Create cable in NetBox."""
        try:
            return self.netbox.create_cable(
                server_interface_id=result.ipmi_interface.interface_id,
                switch_interface_id=result.port_id,
                vlan=result.vlan,
            )
        except Exception as e:
            logger.error(f"Failed to create cable: {e}")
            return None

    def run_daemon(self):
        """Run as daemon with periodic polling."""
        logger.info(f"Starting daemon mode with poll interval {self.config.poll_interval}s")

        while True:
            try:
                self.run_once()
            except Exception as e:
                logger.exception(f"Run failed: {e}")

            logger.info(f"Sleeping for {self.config.poll_interval}s")
            time.sleep(self.config.poll_interval)

    def close(self):
        """Cleanup resources."""
        self.state_db.close()
