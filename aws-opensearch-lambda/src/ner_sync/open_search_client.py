import boto3
from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection


class OpenSearchClient:
    """Create the STS session and signed OpenSearch client."""

    def __init__(self, config):
        self.config = config

    def _assume_access_role(self):
        """Use STS to get a session for the OpenSearch access role."""

        sts_client = boto3.client("sts", region_name=self.config.region)
        sts_response = sts_client.assume_role(
            RoleArn=self.config.iam_access_role,
            RoleSessionName="nerOpenSearchIngestSession",
        )
        credentials = sts_response["Credentials"]

        # This session now uses the temporary credentials returned by STS.
        return boto3.Session(
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
            region_name=self.config.region,
        )

    def create_client(self):
        """Create an OpenSearch client using the assumed role session."""

        iam_session = self._assume_access_role()
        credentials = iam_session.get_credentials()

        # Managed OpenSearch domains use service name es.
        # OpenSearch Serverless uses aoss instead.
        auth = AWSV4SignerAuth(
            credentials,
            self.config.region,
            self.config.service_name,
        )

        client = OpenSearch(
            hosts=[{"host": self.config.host, "port": 443}],
            http_auth=auth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
            pool_maxsize=10,
            timeout=30,
            max_retries=3,
            retry_on_timeout=True,
        )
        breakpoint()
        return client