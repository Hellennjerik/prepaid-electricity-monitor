package com.hellennjerik.prepaid_electricity_monitor

import android.content.Intent
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.bottomnavigation.BottomNavigationView

/** One place that wires the bottom menu for every screen. Call it from onCreate with this screen's own tab id. */
fun AppCompatActivity.setupBottomNav(selectedId: Int) {
    val nav = findViewById<BottomNavigationView>(R.id.bottom_nav)
    nav.selectedItemId = selectedId
    nav.setOnItemSelectedListener { item ->
        if (item.itemId == selectedId) return@setOnItemSelectedListener true
        val target = when (item.itemId) {
            R.id.nav_meter -> DashboardActivity::class.java
            R.id.nav_appliances -> ApplianceActivity::class.java
            R.id.nav_reports -> ReportsActivity::class.java
            R.id.nav_learn -> LearnActivity::class.java
            R.id.nav_profile -> ProfileActivity::class.java
            else -> return@setOnItemSelectedListener false
        }
        startActivity(Intent(this, target).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT or Intent.FLAG_ACTIVITY_NO_ANIMATION))
        overridePendingTransition(0, 0)
        true
    }
}

/** Small helper used by the screens that draw their own views. */
fun AppCompatActivity.dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()