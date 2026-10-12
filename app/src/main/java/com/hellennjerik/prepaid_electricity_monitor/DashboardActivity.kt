package com.hellennjerik.prepaid_electricity_monitor

import android.os.Bundle
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

class DashboardActivity : AppCompatActivity() {

    private lateinit var db: AppDatabase
    private var currentUnits = 100.0 // Simulated starting units for the demo
    private var isMeterRunning = true

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_dashboard)

        db = AppDatabase.getDatabase(this)
        val tvLiveMeter = findViewById<TextView>(R.id.tvLiveMeter)
        val btnLogPurchase = findViewById<Button>(R.id.btnLogPurchase)

        // THIS IS YOUR LIVE METER COROUTINE LOOP
        lifecycleScope.launch {
            while (isMeterRunning) {
                // Simulate using 0.01 units every second
                currentUnits -= 0.01
                tvLiveMeter.text = String.format("%.2f Units", currentUnits)

                // Wait 1 second before updating again
                delay(1000)
            }
        }

        btnLogPurchase.setOnClickListener {
            // Reset units to simulate buying more electricity
            currentUnits = 100.0
            Toast.makeText(this, "100 Units Purchased!", Toast.LENGTH_SHORT).show()
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        isMeterRunning = false // Stop the loop when app closes
    }
}