package com.hellennjerik.prepaid_electricity_monitor

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query

@Dao
interface AlertLogDao {
    @Insert
    suspend fun insertAlert(alert: AlertLog)

    @Query("SELECT * FROM alert_logs WHERE userId = :userId ORDER BY dateSent DESC")
    suspend fun getAlertsForUser(userId: Int): List<AlertLog>
}