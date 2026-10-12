package com.hellennjerik.prepaid_electricity_monitor

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query
import kotlinx.coroutines.flow.Flow

@Dao
interface TokenPurchaseDao {
    @Insert
    suspend fun insertPurchase(purchase: TokenPurchase)

    @Query("SELECT * FROM token_purchases ORDER BY timestamp DESC")
    fun getAllPurchases(): Flow<List<TokenPurchase>>
}