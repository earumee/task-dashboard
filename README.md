# Azure WebDAV

This repository deploys a small WebDAV server to Azure App Service. Files are stored privately in Azure Blob Storage; the App Service's system-assigned managed identity receives `Storage Blob Data Contributor` access.

## Deploy

Create a resource group, then deploy the Bicep template:

```bash
az group create --name rg-webdav --location eastus
az deployment group create \
  --resource-group rg-webdav \
  --template-file infra/main.bicep \
  --parameters webDavPassword='use-a-long-random-password'
```

The deployment output contains the HTTPS endpoint. Connect a WebDAV client to that URL with the configured username and password. Azure App Service enforces HTTPS, and the application supports `OPTIONS`, `PROPFIND`, `GET`, `PUT`, `DELETE`, `MKCOL`, `COPY`, `MOVE`, `LOCK`, and `UNLOCK`.

After the infrastructure deployment, package and upload the application:

```powershell
Compress-Archive -Path app.py,requirements.txt -DestinationPath webdav.zip -Force
az webapp deploy --resource-group rg-webdav --name <appName> --src-path webdav.zip --type zip
```

For local development:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
az login
$env:AZURE_STORAGE_ACCOUNT_URL = "https://<account>.blob.core.windows.net"
$env:AZURE_STORAGE_CONTAINER = "webdav"
$env:WEBDAV_USERNAME = "webdav"
$env:WEBDAV_PASSWORD = "local-password"
uvicorn app:app --reload
```
