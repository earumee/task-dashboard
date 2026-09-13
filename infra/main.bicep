param location string = resourceGroup().location
@secure()
param webDavPassword string
param webDavUsername string = 'webdav'
param storageContainerName string = 'webdav'

var suffix = uniqueString(resourceGroup().id)
var storageName = 'webdav${suffix}'
var planName = 'webdav-plan-${suffix}'
var appName = 'webdav-app-${suffix}'

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource container 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: '${storage.name}/default/${storageContainerName}'
  properties: { publicAccess: 'None' }
}

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: planName
  location: location
  kind: 'linux'
  sku: { name: 'B1', tier: 'Basic' }
  properties: { reserved: true }
}

resource app 'Microsoft.Web/sites@2023-12-01' = {
  name: appName
  location: location
  kind: 'app,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.11'
      alwaysOn: true
      appCommandLine: 'gunicorn -k uvicorn.workers.UvicornWorker -w 2 app:app'
      minTlsVersion: '1.2'
      appSettings: [
        { name: 'WEBDAV_USERNAME'; value: webDavUsername }
        { name: 'WEBDAV_PASSWORD'; value: webDavPassword }
        { name: 'AZURE_STORAGE_ACCOUNT_URL'; value: 'https://${storage.name}.blob.core.windows.net' }
        { name: 'AZURE_STORAGE_CONTAINER'; value: storageContainerName }
      ]
    }
  }
}

resource blobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, app.id, 'Storage Blob Data Contributor')
  scope: storage
  properties: {
    principalId: app.identity.principalId
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
    )
    principalType: 'ServicePrincipal'
  }
}

output appUrl string = 'https://${app.properties.defaultHostName}'
output appName string = app.name
output storageAccount string = storage.name
