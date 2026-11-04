package com.hellennjerik.prepaid_electricity_monitor

import android.content.Intent
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.google.android.material.bottomnavigation.BottomNavigationView
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

class DashboardActivity : AppCompatActivity() {

    private lateinit var tvBalance: TextView
    private lateinit var tvUnits: TextView
    private lateinit var tvRate: TextView
    private lateinit var tvKwh: TextView
    private lateinit var tvSolar: TextView
    private lateinit var tvPrediction: TextView
    private lateinit var tvTariffBadge: TextView
    private lateinit var btnLogPurchase: Button

    private var meterNumber: String = "M001"

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_dashboard)

        meterNumber = intent.getStringExtra("METER_NUMBER") ?: "M001"

        tvBalance = findViewById(R.id.tvBalance)
        tvUnits = findViewById(R.id.tvUnits)
        tvRate = findViewById(R.id.tvRate)
        tvKwh = findViewById(R.id.tvKwh)
        tvSolar = findViewById(R.id.tvSolar)
        tvPrediction = findViewById(R.id.tvPrediction)
        tvTariffBadge = findViewById(R.id.tvTariffBadge)
        btnLogPurchase = findViewById(R.id.btnLogPurchase)

        startLiveUpdates()

        btnLogPurchase.setOnClickListener {
            val input = EditText(this)
            input.hint = "Amount in KSh (e.g. 1000)"
            input.inputType = android.text.InputType.TYPE_CLASS_NUMBER

            AlertDialog.Builder(this)
                .setTitle("Record a top-up")
                .setMessage(
                    "Bought tokens through M-Pesa, *977# or the Kenya Power app? " +
                            "Enter the amount so the app can update your units."
                )
                .setView(input)
                .setPositiveButton("Record") { dialog, _ ->
                    val amount = input.text.toString().toIntOrNull()
                    dialog.dismiss()
                    if (amount != null && amount > 0) {
                        executeTokenPurchase(amount)
                    }
                }
                .setNegativeButton("Cancel") { dialog, _ -> dialog.dismiss() }
                .show()
        }

        val bottomNav = findViewById<BottomNavigationView>(R.id.bottom_nav)
        bottomNav.selectedItemId = R.id.nav_meter

        bottomNav.setOnItemSelectedListener { item ->
            when (item.itemId) {
                R.id.nav_meter -> true
                R.id.nav_appliances -> {
                    val intent = Intent(this, ApplianceActivity::class.java)
                    intent.putExtra("METER_NUMBER", meterNumber)
                    startActivity(intent)
                    true
                }
                R.id.nav_reports -> {
                    val intent = Intent(this@DashboardActivity, ReportsActivity::class.java)
                    intent.putExtra("METER_NUMBER", meterNumber)
                    startActivity(intent)
                    true
                }
                R.id.nav_profile -> {
                    val intent = Intent(this@DashboardActivity, ProfileActivity::class.java)
                    startActivity(intent)
                    true
                }
                else -> false
            }
        }
    }

    // One polling loop that only runs while the screen is visible, and is started exactly once.
    private fun startLiveUpdates() {
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                while (true) {
                    refreshDashboard()
                    delay(5000)
                }
            }
        }
    }

    private suspend fun refreshDashboard() {
        try {
            // --- METER STATUS: balance, tier and price all come from the server ---
            val meterResponse = ApiClient.api.getMeterStatus(meterNumber)
            if (meterResponse.isSuccessful) {
                val meter = meterResponse.body()
                val balanceKsh = meter?.balance ?: 0.0
                val rate = meter?.rate_per_kwh ?: 0.0
                val unitsLeft = if (rate > 0) balanceKsh / rate else 0.0

                tvUnits.text = String.format("%.1f", unitsLeft)
                tvBalance.text = "~ KSh ${String.format("%.2f", balanceKsh)}"
                tvKwh.text = String.format("%.4f", meter?.kwh_used ?: 0.0)
                tvTariffBadge.text = meter?.tier ?: "--"
                tvRate.text = if (rate > 0) "KSh ${String.format("%.2f", rate)} per unit" else ""
            }

            // --- SOLAR OR GRID ONLY ---
            val solarResponse = ApiClient.api.getSolarStatus(meterNumber)
            val solar = if (solarResponse.isSuccessful) solarResponse.body() else null
            val battery = solar?.battery_percent ?: 0.0
            val generation = solar?.generation_kw ?: 0.0
            tvSolar.text = if (battery > 0.0 || generation > 0.0) {
                "$battery% | ${generation}kW"
            } else {
                "Grid only"
            }

            // --- PREDICTION ---
            val predictResponse = ApiClient.api.getPrediction(meterNumber)
            if (predictResponse.isSuccessful) {
                val hoursLeft = predictResponse.body()?.tokens?.hours_until_empty ?: 0.0
                tvPrediction.text = if (hoursLeft >= 24) {
                    "✅ You're fine for ~${String.format("%.0f", hoursLeft / 24.0)} days."
                } else {
                    "⚠️ Warning: Runs out in ~${String.format("%.0f", hoursLeft)} hours!"
                }
            }
        } catch (e: Exception) {
            println("Polling error: ${e.message}")
        }
    }

    private fun executeTokenPurchase(amount: Int) {
        btnLogPurchase.isEnabled = false
        btnLogPurchase.text = "Recording..."

        lifecycleScope.launch {
            try {
                val response = ApiClient.api.buyToken(BuyTokenRequest(meterNumber, amount))
                if (response.isSuccessful) {
                    val data = response.body()
                    AlertDialog.Builder(this@DashboardActivity)
                        .setTitle("Top-up recorded")
                        .setMessage(
                            "Amount: KSh ${data?.amount}\n\n" +
                                    "Units added: ${data?.units} kWh\n\n" +
                                    "Token: ${data?.token}"
                        )
                        .setPositiveButton("OK") { d, _ -> d.dismiss() }
                        .show()
                    refreshDashboard()   // show the new balance now; the polling loop keeps running by itself
                } else {
                    Toast.makeText(this@DashboardActivity, "Could not record top-up", Toast.LENGTH_LONG).show()
                }
            } catch (e: Exception) {
                Toast.makeText(this@DashboardActivity, "Error: ${e.message}", Toast.LENGTH_LONG).show()
            } finally {
                btnLogPurchase.isEnabled = true
                btnLogPurchase.text = "+ Record a top-up"
            }
        }
    }
}