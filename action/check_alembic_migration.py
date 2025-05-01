import argparse
import importlib
import os
import sys

from alembic.operations import Operations
from alembic.runtime.environment import EnvironmentContext
from alembic.runtime.migration import MigrationContext, RevisionStep
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, create_engine
from sqlalchemy.sql import select

SCRIPT_DESCRIPTION = """
This script checks the Alembic version of the latest migration against the database and evaluates its readiness.
If the database is not up to date, it provides recommendations for applying the necessary migrations or make migrations 
automatically if provided `target_metadata` and the --apply-migrations flag is set.
It supports PostgreSQL, MySQL, and SQLite databases.
"""

class AlembicMigrationChecker:
    """
    A utility class for assessing alignment between the database version and Alembic migration scripts.

    This class provides methods to evaluate the readiness of the database for migration updates
    by comparing its version with the latest migration script. It offers insights into migration
    alignment and provides actionable recommendations based on the assessment results.

    Usage:
    1. Initialize the class with the necessary configurations.
    2. Use the 'evaluate_migration_alignment' method to assess migration readiness.

    Methods:
    - evaluate_migration_alignment(): Assesses the database against the latest migration script
      to determine migration readiness and alignment.
      Returns:
        - 0 if the database is up-to-date with the latest migration script or if there are no new migrations detected.
        - 1 if there's a version mismatch or other error.
    """

    def __init__(
        self,
        db_url,
        db_type,
        db_host,
        db_port,
        db_user,
        db_password,
        db_name,
        migrations_path,
        target_metadata=None,
        apply_migrations=False,
    ):
        """
        Initializes the AlembicMigrationChecker with database connection details and migrations folder path.
        If a db_url is given, no other params are required

        :param db_url: The database URL
        :param db_type: The database type (postgresql, mysql, sqlite)
        :param db_host: The database host address
        :param db_port: The database port
        :param db_user: The database user
        :param db_password: The database password
        :param db_name: The database name
        :param migrations_path: The path to Alembic migrations folder
        :param target_metadata: The target metadata for the database
        :param apply_migrations: Flag to apply migrations if there are any pending migrations
        """
        print("Initializing AlembicMigrationChecker...")
        self.db_type = db_type
        self.db_host = db_host
        self.db_port = db_port
        self.db_user = db_user
        self.db_password = db_password
        self.db_name = db_name
        self.migrations_path = migrations_path
        self.target_metadata = target_metadata
        self.apply_migrations = apply_migrations

        if db_url:
            self.db_url = db_url
        else:
            validation_error = self._validate_db_inputs()
            if validation_error:
                raise ValueError(validation_error)
            self.db_url = self._get_database_url()

        self.engine = self._get_database_engine()
        self._alembic_cfg = None
        self._script_directory = None

    def _validate_db_inputs(self):
        """
        Validates the necessary inputs for connecting to a database and accessing the migrations folder path.

        Returns:
            str: An error message string if validation fails, indicating the reason for the failure. Returns None if all validations pass.

        Raises:
            Exception: Catches and returns any exceptions as error messages that occur during the validation of the migrations folder path.

        """
        try:
            # Validate database name
            if not self.db_name:
                return "\nERROR: Database name is required."

            # Validate database type
            if self.db_type not in {"postgresql", "mysql", "sqlite"}:
                return "\nERROR: Invalid database type. Supported types are 'postgresql', 'mysql', and 'sqlite'."

            # Validate inputs for non-SQLite databases
            if self.db_type != "sqlite" and (
                not self.db_host
                or not self.db_port
                or not self.db_user
                or not self.db_password
            ):
                return "\nERROR: Database host, port, user, and password are required for non-SQLite databases."

            # Check migrations path existence
            if not os.path.exists(self.migrations_path):
                print(self.migrations_path)
                return (
                    f"\nERROR: Migrations path '{self.migrations_path}' does not exist."
                )

            return None
        except Exception as e:
            # Handle any exceptions that were raised during validation
            return f"\nERROR during input validation: {e}"

    def _get_database_url(self):
        """Constructs and returns the database URL."""
        if self.db_type == "sqlite":
            return f"sqlite:///{self.db_name}"  # SQLite doesn't use port
        else:
            return f"{self.db_type}://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}"

    def _get_database_engine(self):
        """Creates and returns a SQLAlchemy database engine."""
        print("Creating a SQLAlchemy database engine...")
        try:
            engine = create_engine(self.db_url)
            print("Database engine created successfully.")
            engine.connect()
            print("Database engine connected successfully.")
            return engine
        except Exception as e:
            print("\nERROR creating database engine:", e)
            sys.exit(1)

    @property
    def alembic_config(self):
        """Creates a custom Alembic Config object in memory for accessing migration information."""
        if not self._alembic_cfg:
            self._alembic_cfg = Config()
            self._alembic_cfg.set_main_option("script_location", self.migrations_path)
            self._alembic_cfg.set_main_option("sqlalchemy.url", self.db_url)
        return self._alembic_cfg

    @property
    def script_directory(self):
        """Accesses the Alembic script directory."""
        if not self._script_directory:
            self._script_directory = ScriptDirectory.from_config(self.alembic_config)
        return self._script_directory

    def get_latest_migration_version(self):
        """Returns the latest migration version from the Alembic migrations directory."""
        print(
            "Retrieving the latest migration version from the Alembic migrations directory..."
        )
        head_revision = self.script_directory.get_revision("head")
        if head_revision is not None:
            print("Latest migration version found.")
            return head_revision.revision
        else:
            print("\nERROR: No head revision found in Alembic migrations.")
            return None

    def get_db_version(self):
        """Fetches and returns the current database version from the Alembic version table."""
        print("Attempting to fetch the current database version...")
        try:
            metadata = MetaData()
            metadata.reflect(bind=self.engine)
            alembic_version_table = metadata.tables["alembic_version"]
            query = select(alembic_version_table.c.version_num).limit(1)
            with self.engine.connect() as connection:
                result = connection.execute(query)
                db_version = result.fetchone()[0]
                print(f"Database version fetched successfully.")
                return db_version
        except Exception as e:
            print("\nERROR fetching database version:", e)
            sys.exit(1)

    def evaluate_migration_alignment(self):
        """Assesses the database against the latest migration script to determine migration readiness and alignment."""
        print("Starting migration alignment evaluation...")
        latest_migration_version = self.get_latest_migration_version()
        db_version = self.get_db_version()
        print(
            f"\nLatest Alembic migration version (revision): {latest_migration_version}"
        )
        print(f"Current database Alembic version: {db_version}")

        if latest_migration_version == db_version:
            print(
                "\nSUCCESS: The database version matches the latest migration script's revision ID. "
                "\nNOTICE: No new migrations have been detected.\nIf a new migration was expected but not recognized, "
                "please check the migration script for issues."
            )
            sys.exit(0)
        else:
            current_revision = self.script_directory.get_revision(
                latest_migration_version
            )
            found_revision = False
            pending_migrations_count = 0
            while current_revision is not None:
                if current_revision.revision == db_version:
                    found_revision = True
                    break
                pending_migrations_count += 1
                current_revision = self.script_directory.get_revision(
                    current_revision.down_revision
                )

            if found_revision:
                if pending_migrations_count == 1:
                    print(
                        f"\nThe database is currently at version {db_version}, which aligns with the down "
                        f"revision of the latest migration script, identified by version ({latest_migration_version})."
                        f"This alignment indicates that one pending migration is ready to be applied to bring the "
                        f"database schema up to the latest version."
                    )
                else:
                    print(
                        f"\nThe database is currently at version {db_version}, which corresponds to a "
                        f"version from a previously applied migration.\nHowever, there are currently "
                        f"{pending_migrations_count} new migration scripts ready to be applied to bring the database "
                        f"schema up to the most recent version.\n"
                    )

                if self.apply_migrations:
                    self.apply_database_migrations()
                else:
                    print(
                        "\nWARNING: The database is not up to date with the latest migration scripts.\n"
                        "To apply the pending migrations, please run the migration script manually or use `apply-migrations` flag."
                    )
                    sys.exit(0)
            else:
                print(
                    f"\nERROR: Version mismatch detected.\n"
                    f"The current database version ({db_version}) does not match the `down_revision` of any known "
                    f"migration script.\nImmediate Action Required: Review migration history and scripts for accuracy. "
                    f"Addressing discrepancies is vital for database integrity and smooth migration processes."
                )
                sys.exit(1)

    def apply_database_migrations(self) -> None:
        """Apply alembic database migrations.

        This method will first check if the database is empty (no applied alembic revisions),
        in which case, it uses SQLAlchemy to create all tables and then stamp the database for alembic.

        If the database is not empty, it will apply all necessary migrations, bringing the database
        up to date with the latest revision.
        """

        # Validate inputs for target_metadata
        if self.target_metadata is None:
            print(
                "\nERROR: `target_metadata` is required to apply migrations. "
                "Please provide the full path to the class in the `target_metadata` argument or set `apply_migrations` to False."
            )
            sys.exit(1)

        script = ScriptDirectory.from_config(self.alembic_config)

        def retrieve_migrations(rev: str, ctx: MigrationContext) -> list[RevisionStep]:
            """Retrieve all remaining migrations to be applied to get to "head".

            The returned migrations will be the migrations that will get applied when upgrading.
            """
            migrations = script._upgrade_revs("head", rev)  # pyright: ignore[reportPrivateUsage]

            if len(migrations) > 0:
                print(f"Applying {len(migrations)} database migrations...")
            else:
                print("No database migrations to apply, database is up to date")

            return migrations

        try:
            print("Importing model's MetaData object...")
            sys.path.insert(0, os.getcwd())

            module_path, class_name = self.target_metadata.rsplit('.', 1)
            module = importlib.import_module(module_path)
            base = getattr(module, class_name, None)

            connection = self.engine.connect()
            env_context = EnvironmentContext(self.alembic_config, script)
            env_context.configure(connection=connection, target_metadata=base.metadata, fn=retrieve_migrations)
            context = env_context.get_context()

            current_rev = context.get_current_revision()

            # If there is no current revision, this is a brand-new database
            # instead of going through the migrations, we can instead use metadata.create_all
            # to create all tables and then stamp the database with the head revision.
            if current_rev is None:
                print("Performing initial database setup (creating tables)...")
                base.metadata.create_all(connection)
                context.stamp(script, "head")
                sys.exit(0)

            with Operations.context(context) as _op, context.begin_transaction():
                context.run_migrations()
        except Exception as e:
            print(f"\nERROR applying migrations: {e}")
            sys.exit(1)
        else:
            print(
                "\nSUCCESS: Database migrations applied successfully.\n"
                "The database schema is now up to date with the latest migration scripts."
            )
            sys.exit(0)

def main():
    """The main function of the script."""

    parser = argparse.ArgumentParser(description=SCRIPT_DESCRIPTION)
    parser.add_argument("--db_url", type=str, help="Database URL")
    parser.add_argument("--db_type", type=str, help="Database Type")
    parser.add_argument("--db_host", type=str, help="Database Host")
    parser.add_argument("--db_port", type=str, help="Database Port")
    parser.add_argument("--db_user", type=str, help="Database User")
    parser.add_argument("--db_password", type=str, help="Database Password")
    parser.add_argument("--db_name", type=str, help="Database Name")
    parser.add_argument("--migrations_path", type=str, help="Migrations Path", required=True)
    parser.add_argument("--target_metadata", type=str, help="Target Metadata. Provide the full path to the class.")
    parser.add_argument("--apply_migrations", type=bool, help="Apply migrations if there are any pending migrations.")
    args = parser.parse_args()
    # Initialize the AlembicMigrationChecker class with the unpacked inputs
    checker = AlembicMigrationChecker(
        args.db_url,
        args.db_type,
        args.db_host,
        args.db_port,
        args.db_user,
        args.db_password,
        args.db_name,
        args.migrations_path,
        args.target_metadata,
        args.apply_migrations,
    )
    # Assess the alignment between the database version and the latest migration script.
    checker.evaluate_migration_alignment()


if __name__ == "__main__":
    main()
