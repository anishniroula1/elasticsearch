import boto3
from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection

from app.config import Config


class OpenSearchClient:
    """Create the OpenSearch client for AWS or local Docker."""

    def __init__(self, config: Config):
        self.config = config

    def create_client(self):
        """Use IAM on AWS and the selected local login for Docker."""

        client_options = {}

        if self.config.auth_mode == "iam":
            session = self._assume_access_role()
            credentials = session.get_credentials()
            client_options["http_auth"] = AWSV4SignerAuth(
                credentials,
                self.config.region,
                self.config.service_name,
            )
        elif self.config.auth_mode == "basic":
            client_options["http_auth"] = (
                self.config.username,
                self.config.password,
            )

        return OpenSearch(
            hosts=[{"host": self.config.host, "port": self.config.port}],
            use_ssl=self.config.use_ssl,
            verify_certs=self.config.verify_certs,
            ssl_assert_hostname=self.config.verify_certs,
            ssl_show_warn=False,
            connection_class=RequestsHttpConnection,
            pool_maxsize=10,
            timeout=60,
            max_retries=5,
            retry_on_timeout=True,
            **client_options,
        )

    def _assume_access_role(self):
        """Assume the role and return a session with temporary credentials."""

        sts_client = boto3.client("sts", region_name=self.config.region)
        response = sts_client.assume_role(
            RoleArn=self.config.iam_access_role,
            RoleSessionName="nerOpenSearchApiSession",
        )
        credentials = response["Credentials"]

        return boto3.Session(
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
            region_name=self.config.region,
        )
