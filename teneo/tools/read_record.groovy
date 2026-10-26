// Tool "read_record" in the "Ext Data" toolkit.
// Parameters: collection, recordId        Outputs: record, found, apiCallSuccessful, errorMessage
apiCallSuccessful = false
found = false
errorMessage = ""
record = [:]

try {
    def result = ExtData.store().get(collection.toString(), recordId.toString())
    apiCallSuccessful = true
    if (result != null) {
        found = true
        record = result
    }
} catch (Exception e) {
    errorMessage = e.message
    println("Ext Data read failed: " + e.message)
}
