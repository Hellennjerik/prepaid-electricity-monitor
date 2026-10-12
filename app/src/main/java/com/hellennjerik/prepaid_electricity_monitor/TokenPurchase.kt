package com.hellennjerik.prepaid_electricity_monitor

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "token_purchases")
data class TokenPurchase(
    @PrimaryKey(autoGenerate = true) val id: Int = 0,
    val userId: Int,
    val unitsPurchased: Double,
    val timestamp: Long = System.currentTimeMillis()
)