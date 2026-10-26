// Tool "set_field" in the "Ext Data" toolkit.
// Parameters: collection, recordId, fieldPath, fieldValue     Outputs: apiCallSuccessful, errorMessage
// Changes one value inside an existing record; fieldPath may be nested, e.g. preferences.contact_by.
apiCallSuccessful = false
errorMessage = ""

try {
    def store = ExtData.store()
    def rec = store.get(collection.toString(), recordId.toString())
    if (rec == null) {
        errorMessage = ("No record " + collection + "/" + recordId).toString()
    } else {
        def parts = fieldPath.toString().split('\\.') as List
        def target = rec
        parts.take(parts.size() - 1).each { p ->
            if (!(target[p] instanceof Map)) target[p] = [:]
            target = target[p]
        }
        target[parts.last()] = fieldValue
        store.put(collection.toString(), recordId.toString(), rec)
        apiCallSuccessful = true
    }
} catch (Exception e) {
    errorMessage = e.message
    println("Ext Data set field failed: " + e.message)
}
