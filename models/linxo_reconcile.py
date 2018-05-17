# -*- coding: utf-8 -*-
import logging

from openerp import api, fields, models
import openerp.addons.decimal_precision as dp

_logger = logging.getLogger(__name__)


class linxo_reconcile(models.TransientModel):
    _name = 'linxo.reconcile'

    @api.multi
    def _get_candidates(self):
        """Will return a list of ids of account.move.line according to amount & co
        """
        self.ensure_one()

        transaction = self.transaction_id
        if not transaction:
            return

        _logger.debug('Got transaction %d' % transaction.id)

        search_args = [
            ('journal_id', '=', transaction.journal_id.id),
        ]

        if self.credit:
            search_args.append(('credit', '<=', round(self.credit, 3) + 0.0001))
            search_args.append(('credit', '>=', round(self.credit, 3) - 0.0001))
        else:
            search_args.append(('debit', '<=', round(self.debit, 3) + 0.0001))
            search_args.append(('debit', '>=', round(self.debit, 3) - 0.0001))

        _logger.debug('Search criteria for account.move.line')
        _logger.debug(search_args)
        move_line_obj = self.env['account.move.line']
        account_move_lines = move_line_obj.search(search_args)
        _logger.debug('Pre result for account.move.line')
        _logger.debug(account_move_lines)

        final_ids = []
        for account_move_line in account_move_lines:
            if account_move_line.move_id.state != 'posted':
                final_ids.append(account_move_line.id)

        final_account_move_lines = move_line_obj.browse(final_ids)
        _logger.debug('Final result for account.move.line')
        _logger.debug(final_account_move_lines)
        self.candidates = final_account_move_lines

    @api.multi
    def _get_invoices(self):
        """Will return a list of ids of unpaid account.invoice, matching amount."""
        self.ensure_one()

        transaction = self.transaction_id
        if not transaction:
            return

        _logger.debug('Got transaction %d' % transaction.id)

        search_args = [
            ('state', '=', 'open')
        ]

        if self.credit:
            search_args.append(('amount_total', '>=', round(self.credit, 3) - 0.0001))
            search_args.append(('amount_total', '<=', round(self.credit, 3) + 0.0001))
        else:
            search_args.append(('amount_total', '>=', round(self.debit, 3) - 0.0001))
            search_args.append(('amount_total', '<=', round(self.debit, 3) + 0.0001))

        _logger.debug('Search criteria for account.invoice')
        _logger.debug(search_args)
        invoices = self.env['account.invoice'].search(search_args)
        _logger.debug('Result for account.invoices')
        _logger.debug(invoices)
        self.invoices = invoices

    @api.multi
    def _get_transactions(self):
        """Will return a list of ids of unpaid account.invoice, matching amount."""
        self.ensure_one()

        transaction = self.transaction_id
        if not transaction:
            return

        _logger.debug('Got transaction %d' % transaction.id)

        search_args = [('id', '!=', transaction.id), ('reconciled', '=', False)]
        if self.credit:
            search_args.append(('amount', '=', self.credit))
        else:
            search_args.append(('amount', '=', -self.debit))

        _logger.debug('Search criteria for linxo.transaction')
        _logger.debug(search_args)
        transactions = self.env['linxo.transaction'].search(search_args)
        _logger.debug('Result for linxo.transaction')
        _logger.debug(transactions)
        self.transactions = transactions

    date = fields.Date('Date')
    debit = fields.Float('Debit', digits_compute=dp.get_precision('Account'))
    credit = fields.Float('Credit', digits_compute=dp.get_precision('Account'))
    transaction_id = fields.Many2one('linxo.transaction', 'Original Transaction')
    candidates = fields.One2many('account.move.line',
                                 compute=_get_candidates,
                                 string='Matching Account Move Line')
    invoices = fields.One2many('account.invoice',
                               compute=_get_invoices,
                               string='Matching Unpaid Invoices')
    transactions = fields.One2many('linxo.transaction',
                                   compute=_get_transactions,
                                   string='Matching Reverse Transactions')
    label = fields.Char('Label', related='transaction_id.label')
    notes = fields.Char('Notes', related='transaction_id.notes')
