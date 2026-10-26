// Tool "delete_record" in the "Ext Data" toolkit.
// Parameters: collection, recordId        Outputs: deleted, apiCallSuccessful, errorMessage
apiCallSuccessful = false
deleted = false
errorMessage = ""

try {
    deleted = ExtData.store().delete(collection.toString(), recordId.toString())
    apiCallSuccessful = true
} catch (Exception e) {
    errorMessage = e.message
    println("Ext Data delete failed: " + e.message)
}
