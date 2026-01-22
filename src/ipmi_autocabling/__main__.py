"""Entry point for IPMI Auto-Cabling service."""

import argparse
import os
import sys

from dotenv import load_dotenv

from .config import Config
from .logging_config import setup_logging
from .service import IPMIAutoCablingService


def main():
    parser = argparse.ArgumentParser(
        description="IPMI Auto-Cabling Service for NetBox"
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to .env file (default: .env)",
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (default: INFO)",
    )
    parser.add_argument(
        "--log-format",
        default=os.getenv("LOG_FORMAT", "text"),
        choices=["text", "json", "kv"],
        help="Log format (default: text)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run mode (don't create cables)",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run as daemon with periodic polling",
    )

    args = parser.parse_args()

    if os.path.exists(args.env_file):
        load_dotenv(args.env_file)

    setup_logging(level=args.log_level, format_type=args.log_format)

    config = Config.from_env()

    if args.dry_run:
        config.dry_run = True

    if not config.netbox_url or not config.netbox_token:
        print("Error: NETBOX_URL and NETBOX_TOKEN are required", file=sys.stderr)
        sys.exit(1)

    service = IPMIAutoCablingService(config)

    try:
        if args.daemon or config.poll_interval > 0:
            service.run_daemon()
        else:
            summary = service.run_once()

            if summary.errors > 0:
                sys.exit(2)
    except KeyboardInterrupt:
        print("\nInterrupted")
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        service.close()


if __name__ == "__main__":
    main()
