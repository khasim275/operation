from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from config import get_settings


def _connection_parts() -> dict[str, str]:
    settings = get_settings()
    return conninfo_to_dict(settings.database_dsn)


def _tool(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise RuntimeError(f"{name} was not found on PATH. Install PostgreSQL command-line tools.")
    return executable


def _pg_environment(parts: dict[str, str]) -> dict[str, str]:
    environment = os.environ.copy()
    if parts.get("password"):
        environment["PGPASSWORD"] = parts["password"]
    for key, variable in (
        ("sslmode", "PGSSLMODE"),
        ("sslrootcert", "PGSSLROOTCERT"),
        ("sslcert", "PGSSLCERT"),
        ("sslkey", "PGSSLKEY"),
    ):
        if parts.get(key):
            environment[variable] = parts[key]
    return environment


def _pg_args(parts: dict[str, str]) -> list[str]:
    args = []
    for key, option in (("host", "--host"), ("port", "--port"), ("user", "--username"), ("dbname", "--dbname")):
        if parts.get(key):
            args.extend([option, parts[key]])
    return args


def create_backup(output_dir: Path) -> Path:
    parts = _connection_parts()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = output_dir.resolve() / f"dental-crm-{stamp}"
    destination.mkdir(parents=True, exist_ok=False)
    dump_path = destination / "database.dump"
    subprocess.run(
        [_tool("pg_dump"), "--format=custom", "--no-owner", "--no-acl", "--file", str(dump_path), *_pg_args(parts)],
        check=True,
        env=_pg_environment(parts),
    )
    storage_dir = get_settings().storage_dir.resolve()
    files_path = destination / "uploads.zip"
    with zipfile.ZipFile(files_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if storage_dir.exists():
            for path in storage_dir.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(storage_dir))
    (destination / "manifest.json").write_text(
        json.dumps({"created_at": datetime.now(timezone.utc).isoformat(), "database_dump": dump_path.name,
                    "uploads_archive": files_path.name}, indent=2),
        encoding="utf-8",
    )
    return destination


def restore_test(backup_dir: Path) -> tuple[str, int]:
    parts = _connection_parts()
    dump_path = backup_dir.resolve() / "database.dump"
    upload_path = backup_dir.resolve() / "uploads.zip"
    if not dump_path.is_file() or not upload_path.is_file():
        raise FileNotFoundError("Backup folder must contain database.dump and uploads.zip.")
    test_database = f"dental_crm_restore_{uuid.uuid4().hex[:12]}"
    admin_parts = dict(parts)
    admin_parts["dbname"] = "postgres"
    with psycopg.connect(**admin_parts, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(test_database)))
    try:
        restore_parts = dict(parts)
        restore_parts["dbname"] = test_database
        subprocess.run(
            [
                _tool("pg_restore"), "--exit-on-error", "--no-owner", "--no-acl",
                "--dbname", _dsn_for_parts(restore_parts), str(dump_path),
            ],
            check=True,
            env=_pg_environment(parts),
        )
        with zipfile.ZipFile(upload_path) as archive:
            bad_member = next(
                (member for member in archive.namelist()
                 if Path(member).is_absolute() or ".." in Path(member).parts),
                None,
            )
            if bad_member:
                raise ValueError("Uploads archive contains an unsafe path.")
            with tempfile.TemporaryDirectory(prefix="dental-crm-restore-") as temp_dir:
                archive.extractall(temp_dir)
                restored_files = sum(1 for path in Path(temp_dir).rglob("*") if path.is_file())
        with psycopg.connect(**restore_parts) as connection:
            patient_count = connection.execute("SELECT count(*) FROM patients").fetchone()[0]
            connection.execute("SELECT count(*) FROM audit_log").fetchone()
        return test_database, patient_count + restored_files
    finally:
        with psycopg.connect(**admin_parts, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(test_database))
            )


def _dsn_for_parts(parts: dict[str, str]) -> str:
    keys = ("host", "port", "dbname", "user")
    return " ".join(f"{key}={_quote_conninfo(parts[key])}" for key in keys if parts.get(key))


def _quote_conninfo(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Back up or test-restore the CRM database and uploaded files.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup_parser = subparsers.add_parser("backup")
    backup_parser.add_argument("--output", type=Path, default=Path("backups"))
    restore_parser = subparsers.add_parser("restore-test")
    restore_parser.add_argument("--backup", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.command == "backup":
        print(f"Backup created at: {create_backup(arguments.output)}")
    else:
        database, restored_count = restore_test(arguments.backup)
        print(f"Restore test succeeded in temporary database {database}; verified {restored_count} patient/file records.")
