package com.hellennjerik.prepaid_electricity_monitor

import android.os.Bundle
import android.widget.Button
import android.widget.CheckBox
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch

class ApplianceActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_appliances)

        val meterNumber = intent.getStringExtra("METER_NUMBER") ?: "M001"

        val cbFridge = findViewById<CheckBox>(R.id.cbFridge)
        val cbShower = findViewById<CheckBox>(R.id.cbShower)
        val cbPasi = findViewById<CheckBox>(R.id.cbPasi)
        val cbTV = findViewById<CheckBox>(R.id.cbTV)
        val cbMicrowave = findViewById<CheckBox>(R.id.cbMicrowave)
        val cbPump = findViewById<CheckBox>(R.id.cbPump)

        val btnSave = findViewById<Button>(R.id.btnSaveAppliances)

        btnSave.setOnClickListener {
            val selectedAppliances = mutableListOf<ApplianceItem>()

            if (cbFridge.isChecked) selectedAppliances.add(ApplianceItem("fridge", "always"))
            if (cbShower.isChecked) selectedAppliances.add(ApplianceItem("instant_shower", "sometimes"))
            if (cbPasi.isChecked) selectedAppliances.add(ApplianceItem("pasi", "sometimes"))
            if (cbTV.isChecked) selectedAppliances.add(ApplianceItem("tv", "often"))
            if (cbMicrowave.isChecked) selectedAppliances.add(ApplianceItem("microwave", "rarely"))
            if (cbPump.isChecked) selectedAppliances.add(ApplianceItem("water_pump", "sometimes"))

            if (selectedAppliances.isEmpty()) {
                Toast.makeText(this, "Please select at least one appliance", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            btnSave.isEnabled = false
            btnSave.text = "Analyzing..."

            lifecycleScope.launch {
                try {
                    // 1. Save the appliances
                    val saveResponse = ApiClient.api.registerAppliances(RegisterAppliancesRequest(meterNumber, selectedAppliances))

                    if (saveResponse.isSuccessful) {
                        // 2. Fetch the analysis
                        val analysisResponse = ApiClient.api.analyzeAppliances(meterNumber)

                        if (analysisResponse.isSuccessful) {
                            val data = analysisResponse.body()
                            showAnalysisPopup(data)
                        }
                    }
                } catch (e: Exception) {
                    Toast.makeText(this@ApplianceActivity, "Error: ${e.message}", Toast.LENGTH_LONG).show()
                } finally {
                    btnSave.isEnabled = true
                    btnSave.text = "Analyze My Usage"
                }
            }
        }
    }

    private fun showAnalysisPopup(data: ApplianceAnalysisResponse?) {
        if (data == null) return

        // Build a beautiful text breakdown
        val breakdownText = StringBuilder()
        breakdownText.append("Total Cost: KSh ${data.estimated_monthly_cost} / month\n\n")
        breakdownText.append("Top Consumers:\n")

        // Sort by highest percentage
        val sortedList = data.breakdown.sortedByDescending { it.percent }

        for (item in sortedList) {
            breakdownText.append("• ${item.appliance.replace("_", " ").uppercase()}: ")
            breakdownText.append("${item.percent}% (KSh ${item.ksh_per_month})\n")
        }

        AlertDialog.Builder(this)
            .setTitle("💡 Your Energy Breakdown")
            .setMessage(breakdownText.toString())
            .setPositiveButton("Got it") { dialog, _ -> dialog.dismiss() }
            .show()
    }
}