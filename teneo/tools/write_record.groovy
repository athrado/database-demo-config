// Tool "write_record" in the "Ext Data" toolkit.
// Parameters: collection, recordId, record     Outputs: apiCallSuccessful, errorMessage
// Writes the WHOLE record: take the Map from read_record, change it, and pass it back in.
apiCallSuccessful = false
errorMessage = ""

try {
    ExtData.store().put(collection.toString(), recordId.toString(), (Map) record)
    apiCallSuccessful = true
} catch (Exception e) {
    errorMessage = e.message
    println("Ext Data write failed: " + e.message)
}
