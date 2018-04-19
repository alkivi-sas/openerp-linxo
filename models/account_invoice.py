# -*- coding: utf-8 -*-
import logging

from openerp import models, api, _
from openerp.exceptions import Warning

_logger = logging.getLogger(__name__)


class account_invoice(models.Model):
    _inherit = 'account.invoice'

    @api.one
    def do_reconciliation(self):
        context = self.env.context

        if 'transaction_id' not in context:
            _logger.warning('do_reconciliation problem, context is fucked up')
            _logger.warning(context)
            raise Warning(_("I dont have a transaction associated, this is weird."))

        transaction_id = context['transaction_id']
        transaction = self.env['linxo.transaction'].browse(transaction_id)

        # First part, create voucher
        account = transaction.journal_id.default_credit_account_id or transaction.journal_id.default_debit_account_id

        # Fetch correct period_id according to transaction date
        date = transaction.date
        search_args = [
            ('date_start', '<=', date),
            ('date_stop', '>=', date),
            ('special', '=', False),
            ('company_id', '=', self.company_id.id)
        ]
        periods = self.env['account.period'].search(search_args)
        if not periods:
            raise Warning(_("Unable to find a period for date of transaction %s" % date))
        elif len(periods) > 1:
            raise Warning(_("Found multiple period for date of transaction %s" % date))
        period_id = periods.id

        partner = self.env['res.partner']._find_accounting_partner(self.partner_id)
        partner_id = partner.id

        voucher_data = {
            'partner_id': partner_id,
            'amount': abs(transaction.amount),
            'journal_id': transaction.journal_id.id,
            'date': date,
            'period_id': period_id,
            'account_id': account.id,
            'type': self.type in ('out_invoice', 'out_refund') and 'receipt' or 'payment',
            'reference': self.name,
        }

        _logger.debug('voucher_data')
        _logger.debug(voucher_data)

        voucher = self.env['account.voucher'].create(voucher_data)
        _logger.debug('voucher created')
        _logger.debug(voucher)

        # Equivalent to workflow proform
        voucher.write({'state': 'draft'})

        # Need to create basic account.voucher.line according to the type of invoice need to check stuff ...
        double_check = 0
        for move_line in self.move_id.line_id:
            _logger.debug('Analysing move_line %d' % move_line.id)
            if move_line.product_id:
                _logger.debug('Skipping move_line %d because got product_id and we dont want that' % move_line.id)
                continue

            # According to invoice type
            if self.type in ('out_invoice', 'in_refund'):
                if move_line.debit > 0.0:
                    line_data = {
                        'name': self.number,
                        'voucher_id': voucher.id,
                        'move_line_id': move_line.id,
                        'account_id': self.account_id.id,
                        'partner_id': partner_id,
                        'amount_unreconciled': abs(move_line.debit),
                        'amount_original': abs(move_line.debit),
                        'amount': abs(move_line.debit),
                        'type': 'cr',
                    }
                    _logger.debug('line_data')
                    _logger.debug(line_data)

                    self.env['account.voucher.line'].create(line_data)
                    double_check += 1
            else:
                # In case of invoice with negative amount ...
                if move_line.credit > 0.0 and move_line.credit != move_line.tax_amount:
                    line_data = {
                        'name': self.number,
                        'voucher_id': voucher.id,
                        'move_line_id': move_line.id,
                        'account_id': self.account_id.id,
                        'partner_id': partner_id,
                        'amount_unreconciled': abs(move_line.credit),
                        'amount_original': abs(move_line.credit),
                        'amount': abs(move_line.credit),
                        'type': 'dr',
                    }
                    _logger.debug('line_data')
                    _logger.debug(line_data)

                    self.env['account.voucher.line'].create(line_data)
                    double_check += 1

        # Cautious check to see if we did ok
        if double_check == 0:
            _logger.warning(self)
            _logger.warning(voucher.id)
            raise Warning(_("I did not create any voucher line"))
        elif double_check > 1:
            _logger.warning(self)
            _logger.warning(voucher.id)
            raise Warning(_("I created multiple voucher line ??"))

        # Where the magic happen
        voucher.button_proforma_voucher()
        _logger.info('Invoice was mark as paid')

        # Final step mark the correct account_move _line
        search_args = [
            ('move_id', '=', voucher.move_id.id),
            ('account_id', '=', account.id),
        ]
        move_line_ids = self.env['account.move.line'].search(search_args)
        if len(move_line_ids) != 1:
            _logger.warning('Weird, we should have one')
            _logger.warning(move_line_ids)
        else:
            vals = {'account_move_line_id': move_line_ids.id, 'reconciled': True}
            self.env['linxo.transaction'].browse(transaction_id).write(vals)

        return True
