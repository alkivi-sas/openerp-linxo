# -*- coding: utf-8 -*-
from openerp import models, fields


class linxo_account(models.Model):
    """ Bank Account stored on Linxo """
    _name = "linxo.account"
    _description = "Linxo Bank Account"

    name = fields.Char('Account Name', size=120, required=True)
    linxo_id = fields.Integer('Linxo Account ID', required=True)
    journal_id = fields.Many2one('account.journal', 'Bank Journal', ondelete='cascade')
    account_number = fields.Char('Account Number', size=30, required=True)
    type = fields.Char('Account Type', size=30, required=True)

    _sql_constraints = [
        ('name', 'unique(name)', 'The name of the bank account must be unique'),
        ('account_number', 'unique(account_number)', 'The account number must be unique'),
        ('linxo_id', 'unique(linxo_id)', 'The account number must be unique')
    ]
    _order = 'name asc'
