// Tool "list_records" in the "Ext Data" toolkit.
// Parameters: collection                  Outputs: records, ids, count, apiCallSuccessful, errorMessage
apiCallSuccessful = false
errorMessage = ""
records = [:]
ids = []
count = 0

try {
    records = ExtData.store().all(collection.toString())
    ids = new ArrayList(records.keySet())
    count = records.size()
    apiCallSuccessful = true
} catch (Exception e) {
    errorMessage = e.message
    println("Ext Data list failed: " + e.message)
}
